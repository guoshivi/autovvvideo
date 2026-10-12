"""Deployment-facing tests for the public autovvvideo tree.

These run on any platform with numpy (the development Mac works); the
Windows-only installation steps are exercised by docs/DEPLOY.md on the target
machine.  Nothing here needs a GPU, network, models or a camera.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workbench.ai_endpoint import normalize_endpoint
from workbench.config import RuntimeConfig
from workbench.media import correlate_pcm


class EndpointTests(unittest.TestCase):
    def test_root_url_gets_chat_completions_path(self):
        self.assertEqual(normalize_endpoint('https://api.deepseek.com'),
                         'https://api.deepseek.com/v1/chat/completions')

    def test_v1_url_gets_appended_once(self):
        self.assertEqual(normalize_endpoint('https://api.deepseek.com/v1'),
                         'https://api.deepseek.com/v1/chat/completions')

    def test_full_url_kept_unchanged(self):
        url = 'https://api.deepseek.com/v1/chat/completions'
        self.assertEqual(normalize_endpoint(url), url)

    def test_remote_http_rejected_when_key_present(self):
        with self.assertRaises(ValueError):
            normalize_endpoint('http://api.example.com/v1', has_key=True)

    def test_loopback_http_allowed(self):
        out = normalize_endpoint('http://127.0.0.1:8000/v1', has_key=True)
        self.assertTrue(out.startswith('http://127.0.0.1:8000/'))

    def test_userinfo_and_query_rejected(self):
        with self.assertRaises(ValueError):
            normalize_endpoint('https://user:secret@api.deepseek.com')
        with self.assertRaises(ValueError):
            normalize_endpoint('https://api.deepseek.com?token=1')


class ConfigTests(unittest.TestCase):
    def test_example_config_loads_from_utf8_bom(self):
        raw = (ROOT / 'config.example.json').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'workbench.config.json'
            p.write_bytes(b'\xef\xbb\xbf' + raw.encode('utf-8'))
            cfg = RuntimeConfig.load(p)
            self.assertEqual(cfg.host, '127.0.0.1')
            self.assertTrue(cfg.nas_roots)
            self.assertTrue(cfg.output_root)

    def test_example_configs_carry_no_secret(self):
        for rel in ('config.example.json', 'config/workbench.config.json.example'):
            d = json.loads((ROOT / rel).read_text(encoding='utf-8-sig'))
            self.assertFalse(d.get('ai_api_key'), rel)

    def test_unknown_config_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'cfg.json'
            p.write_text(json.dumps({'host': '127.0.0.1', 'port': 8765,
                                     'nas_roots': ['./materials'], 'bogus': 1}))
            with self.assertRaises(Exception):
                RuntimeConfig.load(p)


class FrozenCorrelationTests(unittest.TestCase):
    """The frozen QA function extracted from reference/v1 must keep working."""

    @staticmethod
    def _signal(seed, n=24000):
        import numpy as np
        rng = np.random.default_rng(seed)
        return (rng.standard_normal(n) * 6000).astype('<i2')

    def test_identical_pcm_passes(self):
        import numpy as np
        x = self._signal(11).astype(np.float64) / 32768.0
        r = correlate_pcm(x, x, 0)
        self.assertTrue(r['ok'], r)

    def test_unrelated_noise_fails(self):
        x = self._signal(11).astype('float64') / 32768.0
        y = self._signal(12).astype('float64') / 32768.0
        self.assertFalse(correlate_pcm(x, y, 0)['ok'])

    def test_all_silent_original_returns_explicit_error(self):
        import numpy as np
        x = np.zeros(4800, dtype='float64')
        r = correlate_pcm(x, x, 0)
        self.assertFalse(r['ok'])
        self.assertIn('silent', r.get('error', ''))

    def test_near_silence_blocks_do_not_poison_the_scan(self):
        x = self._signal(13).astype('float64') / 32768.0
        x[12000:] = 1.0 / 32768.0  # ~ -90 dBFS residue, below the -50 dBFS exemption
        r = correlate_pcm(x, x, 0)
        self.assertTrue(r['ok'], r)


class ScriptHygieneTests(unittest.TestCase):
    def test_install_fails_closed_without_verified_manifest(self):
        text = (ROOT / 'install.ps1').read_text(encoding='utf-8')
        self.assertIn('VERIFIED_BY_PARENT', text)
        self.assertIn('SHA-256', text)

    def test_doctor_and_launcher_ship_with_the_tree(self):
        for rel in ('doctor.ps1', 'deploy/doctor.py', 'launch.vbs', 'launcher.pyw',
                    'deploy/dependencies.json'):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_dependencies_manifest_is_verified(self):
        m = json.loads((ROOT / 'deploy/dependencies.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(m['status'], 'VERIFIED_BY_PARENT')
        for key in ('uv', 'ffmpeg_shared_gpl', 'fdkaac'):
            self.assertTrue(m['assets'][key]['url'], key)
            self.assertTrue(m['assets'][key]['sha256'], key)
        for key in ('embedding', 'tts'):
            model = m['assets']['models'][key]
            self.assertTrue(model['revision'], key)
            self.assertGreaterEqual(len(model['files_sha256']), 10, key)


if __name__ == '__main__':
    unittest.main()

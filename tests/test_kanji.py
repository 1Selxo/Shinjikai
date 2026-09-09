import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import kanji_extract as k


class Response:
    def __init__(self, status=200, text='', data=None):
        self.status_code, self.text, self.data = status, text, data

    def json(self):
        return self.data


class KanjiTests(unittest.TestCase):
    def test_concurrent_shared_asset_download_is_written_once(self):
        class ImageResponse:
            status_code = 200
            headers = {'Content-Type': 'image/gif'}
            content = b'GIF89a-test'
        with tempfile.TemporaryDirectory() as temp:
            client = k.Client()
            with patch.object(client, 'request', return_value=ImageResponse()) as request:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda _: k.asset(client, Path(temp), '/shared.gif'), range(32)))
                self.assertEqual(request.call_count, 1)
                self.assertTrue(all(x == results[0] for x in results))

    def test_only_explicit_missing_counts_as_absence(self):
        self.assertIsNone(k.validate_response(65, Response(400, 'KanjiNotFound')))
        for status in [403, 404, 429, 500]:
            with self.assertRaises(RuntimeError):
                k.validate_response(65, Response(status, 'KanjiNotFound'))
        with self.assertRaises(ValueError):
            k.validate_response(65, Response(data={}))

    def test_preserves_unknown_fields_and_supplementary_characters(self):
        data = {'Kanji': {'Character': ord('𠮷'), 'Readings': [], 'Meanings': [], 'Notes': [], 'FutureField': 'keep'}}
        self.assertEqual(k.validate_response(ord('𠮷'), Response(data=data)), data)
        with self.assertRaises(ValueError):
            k.validate_response(65, Response(data=data))

    def test_pictures_include_nested_notes_and_reject_traversal(self):
        self.assertEqual(list(k.picture_names({'Notes': [{'Pictures': [{'Filename': 'a.JPG'}]}]})), ['a.JPG'])
        with self.assertRaises(ValueError):
            list(k.picture_names({'Filename': '../a.jpg'}))

    def test_census_has_every_scalar_once(self):
        values = list(k.candidates())
        self.assertEqual(len(values), 0x110000 - 0x800)
        self.assertEqual(len(set(values)), len(values))
        self.assertIn(0x10FFFF, values)
        self.assertNotIn(0xD800, values)

    def test_export_bundles_bytes_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = k.open_db(root)
            (root / 'assets').mkdir()
            (root / 'assets/image.gif').write_bytes(b'GIF89a-test')
            media = [{'status': 'downloaded', 'path': 'assets/image.gif', 'sha256': k.sha(b'GIF89a-test')}]
            db.execute('INSERT INTO entries VALUES (?,?,?)', (ord('楽'), '{}', json.dumps(media)))
            db.commit()
            with patch.object(k.Path, 'write_text'):
                manifest = k.export(db, root, root / 'out.zip')
                self.assertFalse(manifest['complete'])
                with zipfile.ZipFile(root / 'out.zip') as z:
                    self.assertEqual(z.read('assets/image.gif'), b'GIF89a-test')
                    self.assertIn('checkpoint.sqlite', z.namelist())
                (root / 'assets/image.gif').write_bytes(b'corrupt')
                with self.assertRaises(ValueError):
                    k.export(db, root, root / 'bad.zip')
            db.close()


if __name__ == '__main__':
    unittest.main()

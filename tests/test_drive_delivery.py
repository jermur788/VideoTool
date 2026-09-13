import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

import drive_delivery
import videotool_gui


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeFiles:
    def __init__(self):
        self.created = []

    def list(self, **kwargs):
        return Request({'files': []})

    def create(self, body, fields):
        self.created.append((body, fields))
        if body['mimeType'] == drive_delivery.FOLDER_MIME:
            return Request({'id': 'folder-1'})
        return Request({'id': 'doc-1', 'webViewLink': 'https://docs.example/doc-1'})


class FakeDocuments:
    def __init__(self):
        self.updates = []

    def batchUpdate(self, documentId, body):
        self.updates.append((documentId, body))
        return Request({'documentId': documentId})


class DriveDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def services(self):
        files = FakeFiles()
        documents = FakeDocuments()
        return (SimpleNamespace(files=lambda: files),
                SimpleNamespace(documents=lambda: documents), files, documents)

    def test_client_setup_must_be_for_a_desktop_app_and_is_private(self):
        source = self.folder / 'download.json'
        source.write_text(json.dumps({'installed': {
            'client_id': 'id', 'client_secret': 'secret',
            'auth_uri': 'https://example/auth', 'token_uri': 'https://example/token'}}))
        target = self.folder / 'private' / 'client.json'
        drive_delivery.save_client_config(source, target)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        bad = self.folder / 'web.json'
        bad.write_text(json.dumps({'web': {'client_id': 'id'}}))
        with self.assertRaisesRegex(drive_delivery.DriveError, 'Desktop app'):
            drive_delivery.save_client_config(bad, target)

    def test_delivery_creates_folder_native_doc_and_local_receipt_once(self):
        self.folder.chmod(0o755)
        response = self.folder / 'VideoTool-Gemini-clip.md'
        response.write_text('# VideoTool Gemini response\n\n- Model: `test`\n\n## Response\n\nAnswer\n')
        drive, docs, files, documents = self.services()
        result = drive_delivery.deliver(response, services=(drive, docs), retry_delays=())
        self.assertEqual(result.document_id, 'doc-1')
        self.assertEqual(result.url, 'https://docs.example/doc-1')
        self.assertEqual(files.created[0][0]['name'], drive_delivery.RESULTS_FOLDER)
        self.assertEqual(files.created[1][0]['parents'], ['folder-1'])
        self.assertEqual(files.created[1][0]['mimeType'], drive_delivery.DOC_MIME)
        self.assertEqual(documents.updates[0][0], 'doc-1')
        requests = documents.updates[0][1]['requests']
        self.assertEqual(requests[0]['insertText']['location']['index'], 1)
        self.assertTrue(any('createParagraphBullets' in request for request in requests))
        self.assertTrue(any(request.get('updateParagraphStyle', {}).get(
            'paragraphStyle', {}).get('namedStyleType') == 'TITLE' for request in requests))
        receipt = drive_delivery.metadata_path(response)
        self.assertTrue(receipt.is_file())
        readable_receipt = drive_delivery.receipt_path(response)
        self.assertIn('https://docs.example/doc-1', readable_receipt.read_text())
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(self.folder.stat().st_mode & 0o777, 0o700)
        second = drive_delivery.deliver(response, services=(drive, docs), retry_delays=())
        self.assertEqual(second.document_id, 'doc-1')
        self.assertEqual(len(files.created), 2)

    def test_changed_local_response_does_not_reuse_old_delivery(self):
        response = self.folder / 'result.md'
        response.write_text('first')
        drive, docs, files, _documents = self.services()
        drive_delivery.deliver(response, services=(drive, docs), retry_delays=())
        response.write_text('changed')
        self.assertIsNone(drive_delivery.previous_delivery(response))
        drive_delivery.deliver(response, services=(drive, docs), retry_delays=())
        self.assertEqual(len(files.created), 4)

    def test_remote_document_prevents_duplicate_when_local_receipt_is_missing(self):
        response = self.folder / 'result.md'
        response.write_text('already delivered')
        class ExistingFiles(FakeFiles):
            def list(self, **kwargs):
                if drive_delivery.FOLDER_MIME in kwargs['q']:
                    return Request({'files': [{'id': 'folder-1', 'name': 'results'}]})
                return Request({'files': [{
                    'id': 'doc-old', 'name': 'Existing result',
                    'webViewLink': 'https://docs.example/doc-old', 'parents': ['folder-1']}]})
        files = ExistingFiles()
        documents = FakeDocuments()
        result = drive_delivery.deliver(
            response,
            services=(SimpleNamespace(files=lambda: files),
                      SimpleNamespace(documents=lambda: documents)), retry_delays=())
        self.assertEqual(result.document_id, 'doc-old')
        self.assertEqual(files.created, [])
        self.assertEqual(documents.updates, [])
        self.assertTrue(drive_delivery.metadata_path(response).is_file())

    def test_gui_drive_failure_keeps_local_response_retryable(self):
        response = self.folder / 'result.md'
        response.write_text('safe local result')
        item = videotool_gui.Conversion(response, response, [])
        calls = []
        def fail(*args, **kwargs):
            raise drive_delivery.DriveError('authorization expired')
        result = videotool_gui.deliver_saved_response(
            item, response, threading.Event(), lambda *args: calls.append(args), fail)
        self.assertIsNone(result)
        self.assertEqual(item.drive_status, 'Failed')
        self.assertEqual(item.drive_error, 'authorization expired')
        self.assertEqual(response.read_text(), 'safe local result')

    def test_readiness_reports_each_setup_stage(self):
        with patch('drive_delivery.sdk_present', return_value=False):
            self.assertEqual(drive_delivery.readiness()[1], 'Google Drive support is not installed.')
        with patch('drive_delivery.sdk_present', return_value=True), \
                patch('drive_delivery.client_path', return_value=self.folder / 'missing'):
            self.assertIn('not set up', drive_delivery.readiness()[1])


if __name__ == '__main__':
    unittest.main()

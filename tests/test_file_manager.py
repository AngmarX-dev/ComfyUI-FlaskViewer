import os
import tempfile
import unittest

from flask import Flask

from file_manager import create_file_manager_blueprint


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.input_dir = os.path.join(self.root, "input")
        self.output_dir = os.path.join(self.root, "output")
        self.temp_dir = os.path.join(self.root, "temp")
        self.viewer_dir = os.path.join(self.root, "viewer")
        for path in (self.input_dir, self.output_dir, self.temp_dir, self.viewer_dir):
            os.makedirs(path)
        self.app = Flask(__name__)
        self.app.register_blueprint(
            create_file_manager_blueprint(self.root, lambda: self.viewer_dir)
        )
        self.client = self.app.test_client()

        import sys
        import types
        self.folder_paths = types.ModuleType("folder_paths")
        self.folder_paths.get_input_directory = lambda: self.input_dir
        self.folder_paths.get_output_directory = lambda: self.output_dir
        self.folder_paths.get_temp_directory = lambda: self.temp_dir
        sys.modules["folder_paths"] = self.folder_paths

    def tearDown(self):
        import sys
        sys.modules.pop("folder_paths", None)
        self.temp.cleanup()

    def test_browse_lists_files_and_directories(self):
        os.mkdir(os.path.join(self.output_dir, "nested"))
        with open(os.path.join(self.output_dir, "sample.txt"), "w", encoding="utf-8") as handle:
            handle.write("ok")
        response = self.client.get("/api/library/browse?root=output")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["entries"][0]["type"], "directory")

    def test_parent_traversal_is_rejected(self):
        response = self.client.get("/api/library/browse?root=output&path=..")
        self.assertEqual(response.status_code, 400)

    def test_unknown_root_is_rejected(self):
        response = self.client.get("/api/library/browse?root=system")
        self.assertEqual(response.status_code, 404)

    def test_preview_rejects_non_images(self):
        path = os.path.join(self.output_dir, "notes.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not an image")
        response = self.client.get("/api/library/preview?root=output&path=notes.txt")
        self.assertEqual(response.status_code, 415)

    def test_storage_summary_has_volume_list(self):
        response = self.client.get("/api/library/storage")
        self.assertEqual(response.status_code, 200)
        self.assertIn("volumes", response.get_json())


if __name__ == "__main__":
    unittest.main()

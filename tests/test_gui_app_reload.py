import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gui_app


class FakeStringVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class ReloadScannedModelDataTest(unittest.TestCase):
    def test_reload_scanned_model_data_loads_current_model_file_and_restores_saved_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            model_name = "BMW 테스트 모델"
            model_url = "https://shop.bmw.co.kr/online/oom/TEST123"
            fields = [
                {
                    "kind": "option",
                    "name": "trim:익스테리어",
                    "selector": "",
                    "label": "익스테리어",
                    "options": [
                        {"text": "흰색", "value": "white"},
                        {"text": "검정", "value": "black"},
                    ],
                }
            ]
            config = {"__fields__": {"trim:익스테리어": "검정"}}

            with mock.patch.object(gui_app, "BASE_DIR", tmp_path), \
                 mock.patch.object(gui_app, "CONFIG_FILE", tmp_path / "user_config.json"), \
                 mock.patch.object(gui_app, "PRODUCT_URL", model_url), \
                 mock.patch.dict(gui_app.DISCOVERED_PRODUCT_MODELS, {model_url: model_name}, clear=True):
                gui_app.fields_file_for_url(model_url).write_text(
                    json.dumps(fields, ensure_ascii=False),
                    encoding="utf-8",
                )
                gui_app.config_file_for_url(model_url).write_text(
                    json.dumps(config, ensure_ascii=False),
                    encoding="utf-8",
                )

                app = gui_app.BMWApp.__new__(gui_app.BMWApp)
                app._models = {model_name: model_url}
                app._model_var = FakeStringVar(model_name)
                app._url_var = FakeStringVar("")
                app._field_widgets = []
                app._rendered_fields = None
                app._statuses = []
                app._logs = []

                def render_fields(rendered):
                    app._rendered_fields = rendered
                    var = FakeStringVar("")
                    app._field_widgets = [(rendered[0], var)]

                app._render_fields = render_fields
                app._set_status = app._statuses.append
                app._log = app._logs.append

                app._reload_scanned_model_data()

            self.assertEqual(app._url_var.get(), model_url)
            self.assertEqual(app._rendered_fields, fields)
            self.assertEqual(app._field_widgets[0][1].get(), "검정")
            self.assertIn("최신 데이터가 화면에 반영", app._statuses[-1])
            self.assertEqual(app._logs, [])


if __name__ == "__main__":
    unittest.main()

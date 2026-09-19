import json
import threading
import urllib.error
import urllib.request

import pytest

from jevcraft.agents import RuleBasedProvider
from jevcraft.bwapi.server import BridgeApplication, make_server
from jevcraft.loop import AgentLoop


def test_real_http_step_end_and_wrong_map(observation, tmp_path):
    app = BridgeApplication(lambda: AgentLoop(RuleBasedProvider(), tmp_path), observation.map_name)
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"

    def post(path, payload):
        request = urllib.request.Request(
            url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    try:
        with urllib.request.urlopen(url + "/health") as response:
            assert json.load(response)["protocol_version"] == 1
        bad = observation.model_dump()
        bad["map_name"] = "wrong-map"
        with pytest.raises(urllib.error.HTTPError) as error:
            post("/step", bad)
        assert error.value.code == 400
        result = post("/step", observation.model_dump())
        assert result["match_id"] == observation.match_id
        assert result["action_id"].startswith("gather_")
        final = observation.model_dump()
        final.update(frame=6, ended=True, result="win")
        summary = post("/end", final)
        assert summary["result"] == "win"
        assert summary["completed"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_single_inflight_request(observation, tmp_path):
    app = BridgeApplication(lambda: AgentLoop(RuleBasedProvider(), tmp_path), observation.map_name)
    with app.lock:
        with pytest.raises(ValueError, match="flight"):
            app.handle("/step", observation.model_dump())

"""Seed Demo B: four deterministic shell jobs gated by the pytest evaluator.

Run after the api is up:
    uvicorn flotilla.app:create_app --factory --port 8000   # with FLOTILLA_START_SCHEDULER=1
    python demo/write_pytest_demo.py
Then open http://localhost:8000  (the dashboard) and watch the 4 tasks.
"""

import json
import urllib.request
import uuid

BASE = "http://localhost:8000"


def post(path: str, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req).read()


def main() -> None:
    post("/projects", {"id": "demo", "name": "pytest lifecycle demo"})
    run_id = uuid.uuid4().hex[:8]
    tasks = [
        {
            "id": f"wp-{run_id}-{i}",
            "name": f"pytest job {i}",
            "runtime": "shell",
            "evaluator": "pytest",
            "spec": (
                "Create a small doubler module and its pytest test, then let the "
                "configured evaluator gate completion."
            ),
            "metadata": {
                "command": (
                    "python - <<'PY'\n"
                    "from pathlib import Path\n"
                    "Path('doubler.py').write_text("
                    '"def double(value):\\n    return value * 2\\n")\n'
                    f"Path('test_doubler.py').write_text("
                    f'"from doubler import double\\n\\n'
                    f'def test_double_{i}():\\n    assert double({i + 1}) == {(i + 1) * 2}\\n")\n'
                    "PY"
                )
            },
        }
        for i in range(4)
    ]
    post("/projects/demo/tasks", tasks)
    print(f"seeded {len(tasks)} tasks — open {BASE}")


if __name__ == "__main__":
    main()

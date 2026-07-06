"""Seed Demo B: 4 'write pytest' tasks (shell runtime + pytest evaluator).

Run after the api is up:
    uvicorn flotilla.app:create_app --factory --port 8000   # with FLOTILLA_START_SCHEDULER=1
    python demo/write_pytest_demo.py
Then open http://localhost:8000  (the dashboard) and watch the 4 tasks.
"""
import json
import urllib.request

BASE = "http://localhost:8000"


def post(path: str, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req).read()


def main() -> None:
    post("/projects", {"id": "demo", "name": "write-pytest demo"})
    tasks = [
        {
            "id": f"wp-{i}",
            "name": f"test module {i}",
            "runtime": "shell",
            "evaluator": "pytest",
            "spec": (
                "Write a pytest test file for a function that doubles its input. "
                "Place test_doubler.py in the workspace."
            ),
        }
        for i in range(4)
    ]
    post("/projects/demo/tasks", tasks)
    print(f"seeded {len(tasks)} tasks — open {BASE}")


if __name__ == "__main__":
    main()

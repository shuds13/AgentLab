import importlib
import itertools
import json
import re
import sys
import types
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
FRAMEWORK = ROOT / "framework"
MODULES = (
    "agent",
    "critic",
    "engineer",
    "secretary",
    "tools",
    "transfer",
    "framework.agent",
    "framework.critic",
    "framework.engineer",
    "framework.secretary",
    "framework.tools",
    "framework.transfer",
    "framework.lab_config",
    "framework.watch",
    "framework.slack_to_board",
)


@dataclass
class Turn:
    messages: list
    expected_query: object = None
    query_error: Exception | None = None
    stream_error: Exception | None = None
    stream_error_after: int | None = None


class FakeClaude:
    def __init__(self):
        self.client_plans = deque()
        self.clients = []
        self.options = []
        self.agent_definitions = []
        self.tools = []
        self.mcp_servers = []
        self.module = types.ModuleType("claude_agent_sdk")
        self._build_module()

    def _build_module(self):
        harness = self

        class AssistantMessage:
            def __init__(self, content=None, parent_tool_use_id=None):
                self.content = list(content or [])
                self.parent_tool_use_id = parent_tool_use_id

        class ResultMessage:
            def __init__(self, subtype="success", session_id=None):
                self.subtype = subtype
                self.session_id = session_id

        class ClaudeAgentOptions:
            def __init__(self, **kwargs):
                self.kwargs = dict(kwargs)
                self.__dict__.update(kwargs)
                harness.options.append(self)

        class AgentDefinition:
            def __init__(self, **kwargs):
                self.kwargs = dict(kwargs)
                self.__dict__.update(kwargs)
                harness.agent_definitions.append(self)

        class ClaudeSDKClient:
            def __init__(self, *, options):
                self.options = options
                self.queries = []
                self.context_calls = 0
                self.receive_calls = 0
                self.enter_count = 0
                self.exit_calls = []
                self._active_turn = None
                self._response_read = False
                self._plan = (
                    harness.client_plans.popleft()
                    if harness.client_plans
                    else {
                        "turns": deque(),
                        "contexts": deque(),
                        "enter_error": None,
                        "exit_error": None,
                    }
                )
                harness.clients.append(self)

            async def __aenter__(self):
                self.enter_count += 1
                if self._plan["enter_error"]:
                    raise self._plan["enter_error"]
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                self.exit_calls.append((exc_type, exc, traceback))
                if self._plan["exit_error"]:
                    raise self._plan["exit_error"]
                return False

            async def query(self, prompt):
                self.queries.append(prompt)
                if not self._plan["turns"]:
                    raise AssertionError(f"unexpected Claude query: {prompt!r}")
                turn = self._plan["turns"].popleft()
                expected = turn.expected_query
                if isinstance(expected, str) and prompt != expected:
                    raise AssertionError(f"expected query {expected!r}, got {prompt!r}")
                if hasattr(expected, "search") and not expected.search(prompt):
                    raise AssertionError(
                        f"query did not match {expected.pattern!r}: {prompt!r}"
                    )
                if callable(expected) and not expected(prompt):
                    raise AssertionError(f"query predicate rejected: {prompt!r}")
                if turn.query_error:
                    raise turn.query_error
                self._active_turn = turn
                self._response_read = False

            async def receive_response(self):
                self.receive_calls += 1
                if self._active_turn is None or self._response_read:
                    raise AssertionError("receive_response requires one unread query")
                self._response_read = True
                turn = self._active_turn
                self._active_turn = None
                for index, message in enumerate(turn.messages):
                    if turn.stream_error_after == index:
                        raise turn.stream_error
                    yield message
                if turn.stream_error_after == len(turn.messages):
                    raise turn.stream_error

            async def get_context_usage(self):
                self.context_calls += 1
                contexts = self._plan["contexts"]
                value = contexts.popleft() if contexts else None
                if callable(value):
                    value = value()
                if hasattr(value, "__await__"):
                    value = await value
                if isinstance(value, BaseException):
                    raise value
                return value

        def tool(name, description, schema):
            def decorate(fn):
                fn._tool_name = name
                fn._tool_description = description
                fn._tool_schema = schema
                harness.tools.append(fn)
                return fn

            return decorate

        def create_sdk_mcp_server(**kwargs):
            server = types.SimpleNamespace(**kwargs)
            harness.mcp_servers.append(server)
            return server

        self.module.AssistantMessage = AssistantMessage
        self.module.ResultMessage = ResultMessage
        self.module.ClaudeAgentOptions = ClaudeAgentOptions
        self.module.AgentDefinition = AgentDefinition
        self.module.ClaudeSDKClient = ClaudeSDKClient
        self.module.tool = tool
        self.module.create_sdk_mcp_server = create_sdk_mcp_server

    def plan_client(self, *, turns=(), contexts=(), enter_error=None, exit_error=None):
        self.client_plans.append(
            {
                "turns": deque(turns),
                "contexts": deque(contexts),
                "enter_error": enter_error,
                "exit_error": exit_error,
            }
        )

    def turn(
        self,
        *messages,
        expected_query=None,
        query_error=None,
        stream_error=None,
        stream_error_after=None,
    ):
        return Turn(
            list(messages),
            expected_query,
            query_error,
            stream_error,
            stream_error_after,
        )

    def text(self, text):
        return types.SimpleNamespace(text=text)

    def tool_use(self, name, input=None, id=None):
        return types.SimpleNamespace(name=name, input=input or {}, id=id)

    def assistant(self, *blocks, parent_tool_use_id=None):
        return self.module.AssistantMessage(blocks, parent_tool_use_id)

    def result(self, subtype="success", session_id=None):
        return self.module.ResultMessage(subtype, session_id)

    @staticmethod
    def context(*, tokens, window, percentage, model):
        return {
            "totalTokens": tokens,
            "rawMaxTokens": window,
            "percentage": percentage,
            "model": model,
        }

    @property
    def last_client(self):
        return self.clients[-1]


class FakeGlobusFuture(Future):
    def __init__(self, task_id=None):
        super().__init__()
        self.task_id = task_id

    @classmethod
    def pending(cls, task_id=None):
        return cls(task_id)

    @classmethod
    def successful(cls, value, task_id=None):
        future = cls(task_id)
        future.set_result(value)
        return future

    @classmethod
    def failed(cls, error, task_id=None):
        future = cls(task_id)
        future.set_exception(error)
        return future


@dataclass
class SubmitPlan:
    kind: str
    value: object = None
    task_id: str | None = None


class FakeGlobus:
    def __init__(self):
        self.submit_plans = deque()
        self.endpoint_status = {"status": "online"}
        self.endpoint_metadata = {"name": "fake-endpoint"}
        self.task_statuses = {}
        self.executor_inits = []
        self.serializer_inits = []
        self.strategy_inits = []
        self.submits = []
        self.shutdowns = []
        self.client_inits = []
        self.endpoint_status_calls = []
        self.endpoint_metadata_calls = []
        self.task_calls = []
        self.futures = []
        self._task_ids = itertools.count(1)
        self.module, self.serialize_module = self._build_modules()

    def _build_modules(self):
        harness = self
        root = types.ModuleType("globus_compute_sdk")
        serialize = types.ModuleType("globus_compute_sdk.serialize")

        class AllCodeStrategies:
            def __init__(self):
                harness.strategy_inits.append(self)

        class ComputeSerializer:
            def __init__(self, *, strategy_code):
                self.strategy_code = strategy_code
                harness.serializer_inits.append(self)

        class Executor:
            def __init__(self, *, endpoint_id, user_endpoint_config):
                self.endpoint_id = endpoint_id
                self.user_endpoint_config = user_endpoint_config
                self.serializer = None
                self._stopped = False
                harness.executor_inits.append(self)

            def submit(self, fn, args, target):
                harness.submits.append((self, fn, args, target))
                plan = (
                    harness.submit_plans.popleft()
                    if harness.submit_plans
                    else SubmitPlan("pending")
                )
                if plan.kind == "raise":
                    raise plan.value
                task_id = plan.task_id or f"fake-task-{next(harness._task_ids)}"
                if plan.kind == "pending":
                    future = FakeGlobusFuture.pending(task_id)
                elif plan.kind == "success":
                    future = FakeGlobusFuture.successful(plan.value, task_id)
                elif plan.kind == "error":
                    future = FakeGlobusFuture.failed(plan.value, task_id)
                else:
                    raise AssertionError(f"unknown submit plan {plan.kind}")
                harness.futures.append(future)
                return future

            def shutdown(self, *, wait):
                self._stopped = True
                harness.shutdowns.append((self, wait))

        class Client:
            def __init__(self):
                harness.client_inits.append(self)

            def get_endpoint_status(self, endpoint_id):
                harness.endpoint_status_calls.append(endpoint_id)
                return harness._resolve(harness.endpoint_status, endpoint_id)

            def get_endpoint_metadata(self, endpoint_id):
                harness.endpoint_metadata_calls.append(endpoint_id)
                return harness._resolve(harness.endpoint_metadata, endpoint_id)

            def get_task(self, task_id):
                harness.task_calls.append(task_id)
                return harness._resolve(
                    harness.task_statuses.get(task_id, {"status": "running"}), task_id
                )

        root.Executor = Executor
        root.Client = Client
        root.serialize = serialize
        serialize.ComputeSerializer = ComputeSerializer
        serialize.AllCodeStrategies = AllCodeStrategies
        return root, serialize

    @staticmethod
    def _resolve(value, *args):
        value = value(*args) if callable(value) else value
        if isinstance(value, BaseException):
            raise value
        return value

    def plan_pending(self, task_id=None):
        self.submit_plans.append(SubmitPlan("pending", task_id=task_id))

    def plan_success(self, value, task_id=None):
        self.submit_plans.append(SubmitPlan("success", value, task_id))

    def plan_future_error(self, error, task_id=None):
        self.submit_plans.append(SubmitPlan("error", error, task_id))

    def plan_submit_error(self, error):
        self.submit_plans.append(SubmitPlan("raise", error))


class FakeTask:
    def __init__(self, name, *, remote=True, local=False, bucket=False):
        self.module = types.ModuleType(name)
        self.job_key_calls = []
        self.bucket_calls = []
        self.remote_calls = []
        self.local_calls = []
        self.preflight_result = None
        if remote:
            self.module.JOB_DESC = "fake remote job"
            self.module.JOB_SCHEMA = {"value": int}
            self.module.job_key = self.job_key
            self.module.remote_fn = self.remote_fn
        if local:
            self.module.LOCAL_DESC = "fake local job"
            self.module.LOCAL_SCHEMA = {"value": int}
            self.module.local_fn = self.local_fn
        if bucket:
            self.module.bucket_for = self.bucket_for

    def job_key(self, args):
        self.job_key_calls.append(args)
        return f"value={args.get('value')}"

    def remote_fn(self, args, target):
        self.remote_calls.append((args, target))
        return {"value": args.get("value")}

    def local_fn(self, args):
        self.local_calls.append(args)
        return {"value": args.get("value")}

    def bucket_for(self, args):
        self.bucket_calls.append(args)
        return args.get("bucket", "default")


@dataclass
class ToolsLab:
    root: Path
    campaign_dir: Path
    workspace: Path
    tools: object
    globus: FakeGlobus
    task: FakeTask


@pytest.fixture
def fake_claude_sdk(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake.module)
    return fake


@pytest.fixture
def fake_globus_sdk(monkeypatch):
    fake = FakeGlobus()
    monkeypatch.setitem(sys.modules, "globus_compute_sdk", fake.module)
    monkeypatch.setitem(
        sys.modules, "globus_compute_sdk.serialize", fake.serialize_module
    )
    return fake


@pytest.fixture
def import_module(monkeypatch, fake_claude_sdk):
    def load(name, env=None):
        for module_name in MODULES:
            sys.modules.pop(module_name, None)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        monkeypatch.syspath_prepend(str(FRAMEWORK))
        return importlib.import_module(name)

    return load


@pytest.fixture
def transfer_module(import_module):
    return import_module("transfer")


@pytest.fixture
def tools_lab(tmp_path, monkeypatch, fake_claude_sdk, fake_globus_sdk):
    counter = itertools.count(1)
    loaded = []

    def load(*, remote=True, local=False, bucket=False, task_mutator=None, env=None):
        index = next(counter)
        root = tmp_path / f"lab-{index}"
        campaign_dir = root / "campaigns" / "test-campaign"
        workspace = root / "workspace" / "test-campaign"
        system_dir = root / "systems"
        campaign_dir.mkdir(parents=True)
        workspace.mkdir(parents=True)
        system_dir.mkdir()

        campaign = {
            "system": "test-system",
            "max_concurrent": 2,
            "local_max_concurrent": 2,
            "target": {"env": {"CAMPAIGN_ENV": "yes"}},
        }
        system = {
            "ppn": 4,
            "target": {"env": {"SYSTEM_ENV": "yes"}},
            "bucket_defaults": {
                "queue": "test",
                "walltime": "00:10:00",
                "num_nodes": 1,
            },
        }
        (campaign_dir / "campaign.json").write_text(json.dumps(campaign))
        (system_dir / "test-system.json").write_text(json.dumps(system))
        for name in ("prompt.md", "user_prompt.md", "method.md"):
            (campaign_dir / name).write_text(name)

        if remote:
            user_dir = root / "users" / "test-user"
            user_dir.mkdir(parents=True)
            (user_dir / "test-system.json").write_text(
                json.dumps(
                    {
                        "endpoint": "test-endpoint",
                        "account": "test-account",
                        "work_dir": "/remote/work",
                    }
                )
            )

        task_name = f"_agentlab_test_task_{index}"
        task = FakeTask(task_name, remote=remote, local=local, bucket=bucket)
        if task_mutator:
            task_mutator(task.module)
        monkeypatch.setitem(sys.modules, task_name, task.module)

        for module_name in MODULES:
            sys.modules.pop(module_name, None)
        monkeypatch.syspath_prepend(str(FRAMEWORK))
        values = {
            "LAB_DIR": str(root),
            "CAMPAIGN": "test-campaign",
            "CAMPAIGN_DIR": str(campaign_dir),
            "WORKSPACE_DIR": str(workspace),
            "USER_NAME": "test-user",
            "TASK_DIR": str(campaign_dir),
            "TASK_MODULE": task_name,
            "MAX_SUBMITS": "10",
            "MAX_CONCURRENT": "2",
            "LOCAL_MAX_CONCURRENT": "2",
            "RUN_ID": "test-run",
            "NOTIFY_SCRIPT": "",
        }
        values.update(env or {})
        for key, value in values.items():
            monkeypatch.setenv(key, str(value))
        tools = importlib.import_module("tools")
        lab = ToolsLab(root, campaign_dir, workspace, tools, fake_globus_sdk, task)
        loaded.append(lab)
        return lab

    yield load

    for lab in loaded:
        lab.tools.shutdown_executor()

"""Provider adapters for the research-agent runtime."""

import json
import os
import subprocess
from glob import glob


class ClaudeSession:
    def __init__(self, system_prompt, server, allowed_tools, cwd):
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
        self._client = ClaudeSDKClient(options=ClaudeAgentOptions(
            mcp_servers={"cas": server},
            allowed_tools=allowed_tools,
            permission_mode="bypassPermissions",
            system_prompt=system_prompt,
            cwd=cwd,
        ))

    async def __aenter__(self):
        self.client = await self._client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._client.__aexit__(*args)

    async def query(self, prompt):
        await self.client.query(prompt)

    async def drain_turn(self, round_num):
        from claude_agent_sdk import AssistantMessage, ResultMessage
        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        print(block.text, flush=True)
            elif isinstance(message, ResultMessage):
                print(f"\\n[round {round_num} turn end] {message.subtype}", flush=True)

    async def get_context_usage(self):
        try:
            return await self.client.get_context_usage()
        except Exception:
            return None


class OpenAICompatibleSession:
    """A small Chat Completions agent loop with local function tools."""

    def __init__(self, config, system_prompt, tool_specs, tool_functions):
        from openai import AsyncOpenAI

        api_key = os.environ.get(config["api_key_env"]) if config.get("api_key_env") else None
        kwargs = {"api_key": api_key or "unused"}
        if config.get("base_url"):
            kwargs["base_url"] = config["base_url"]
        self.client = AsyncOpenAI(**kwargs)
        self.model_name = config["model"]
        self.messages = [{"role": "system", "content": system_prompt}]
        self.tool_specs = tool_specs
        self.tool_functions = tool_functions

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.close()

    async def query(self, prompt):
        self.messages.append({"role": "user", "content": prompt})
        while True:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=self.messages,
                tools=self.tool_specs,
                tool_choice="auto",
            )
            message = response.choices[0].message
            assistant = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                assistant["tool_calls"] = [call.model_dump() for call in message.tool_calls]
            self.messages.append(assistant)
            if message.content:
                print(message.content, flush=True)
            if not message.tool_calls:
                return
            for call in message.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                    result = await _invoke_tool(self.tool_functions[call.function.name], args)
                    content = _tool_result_text(result)
                except Exception as exc:
                    content = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": content,
                })

    async def get_context_usage(self):
        return {"model": self.model_name}


async def _invoke_tool(function, args):
    """Call either a plain async function or a Claude SDK decorated function."""
    result = function(args)
    if hasattr(result, "__await__"):
        return await result
    return result


def _tool_result_text(result):
    if isinstance(result, dict) and "content" in result:
        content = result.get("content") or []
        return "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
    return json.dumps(result, default=str)


def local_tool_specs():
    return [
        {"type": "function", "function": {
            "name": "read_file", "description": "Read a text file, optionally by line range.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "start_line": {"type": "integer"},
                "max_lines": {"type": "integer"}}, "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "write_file", "description": "Write or append text to a file.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"},
                "append": {"type": "boolean"}}, "required": ["path", "content"]}}},
        {"type": "function", "function": {
            "name": "glob_files", "description": "Find files matching a glob pattern.",
            "parameters": {"type": "object", "properties": {
                "pattern": {"type": "string"}}, "required": ["pattern"]}}},
        {"type": "function", "function": {
            "name": "grep_text", "description": "Search text in files with a regular expression.",
            "parameters": {"type": "object", "properties": {
                "pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"]}}},
        {"type": "function", "function": {
            "name": "shell", "description": "Run a shell command in the agent working directory.",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string"}}, "required": ["command"]}}},
    ]


def local_tool_functions(root=None):
    root = os.path.abspath(root or os.getcwd())

    def safe_path(path):
        candidate = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
        if os.path.commonpath((root, candidate)) != root:
            raise ValueError(f"path is outside the agent root: {path}")
        return candidate

    async def read_file(args):
        path = safe_path(args["path"])
        start = max(int(args.get("start_line", 1)), 1)
        limit = int(args.get("max_lines", 2000))
        with open(path) as f:
            lines = f.readlines()
        return {"path": path, "start_line": start,
                "content": "".join(lines[start - 1:start - 1 + limit])}

    async def write_file(args):
        path = safe_path(args["path"])
        mode = "a" if args.get("append") else "w"
        with open(path, mode) as f:
            f.write(args["content"])
        return {"path": path, "written": True}

    async def glob_files(args):
        pattern = (args["pattern"] if os.path.isabs(args["pattern"])
                   else os.path.join(root, args["pattern"]))
        return {"matches": glob(pattern, recursive=True)}

    async def grep_text(args):
        import re
        pattern = re.compile(args["pattern"])
        search_root = safe_path(args.get("path") or ".")
        matches = []
        paths = [search_root] if os.path.isfile(search_root) else glob(os.path.join(search_root, "**/*"), recursive=True)
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, errors="replace") as f:
                    for number, line in enumerate(f, 1):
                        if pattern.search(line):
                            matches.append(f"{path}:{number}:{line.rstrip()}")
            except (OSError, UnicodeError):
                continue
        return {"matches": matches[:500]}

    async def shell(args):
        proc = subprocess.run(args["command"], shell=True, cwd=root, text=True,
                              capture_output=True, timeout=300)
        return {"returncode": proc.returncode, "stdout": proc.stdout[-10000:],
                "stderr": proc.stderr[-10000:]}

    return {"read_file": read_file, "write_file": write_file,
            "glob_files": glob_files, "grep_text": grep_text, "shell": shell}

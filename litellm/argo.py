"""Fit requests to Argo, Argonne's model gateway, for the models it serves on its
OpenAI-style chat endpoint (GPT, Gemini).

Argo differs from the OpenAI API in two ways that requests through LiteLLM run into:

- It reads `user` as the caller's username. The agent's requests carry their session id
  there, which Argo refuses. Argo is given the username as the api key instead, so
  `user` is dropped.
- Its chat endpoint takes a message's content only as a string where the standard also
  allows a list of parts: for a user message when tools are sent (GPT), and for the
  system message (Gemini). Text parts are joined into one string.

Loaded by argo.yaml (`callbacks: argo.handler`), which a lab's config.yaml includes.

It touches only requests whose api_base is Argo's.
"""

from litellm.integrations.custom_logger import CustomLogger


def _text(content):
    """A message's content as a string, when it is a list of text parts; else as it was."""
    if not isinstance(content, list):
        return content
    if not all(isinstance(p, dict) and p.get("type") == "text" for p in content):
        return content
    return "\n\n".join(p.get("text", "") for p in content)


class ArgoHook(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        params = kwargs.get("litellm_params") or {}
        base = str(kwargs.get("api_base") or params.get("api_base") or "")
        if "argoapi" not in base:
            return None
        kwargs.pop("user", None)
        messages = kwargs.get("messages")
        if isinstance(messages, list):
            kwargs["messages"] = [dict(m, content=_text(m.get("content")))
                                  if isinstance(m, dict) else m for m in messages]
        return kwargs


handler = ArgoHook()

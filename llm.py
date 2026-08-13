"""
Provider-agnostic LLM caller. Pure stdlib — no pip install needed.

Pick a backend with the LLM_PROVIDER env var:

    gemini     GEMINI_API_KEY      free tier, recommended default
    groq       GROQ_API_KEY        free tier, very fast
    openai     OPENAI_API_KEY      paid
    anthropic  ANTHROPIC_API_KEY   paid
    ollama     (none)              fully local, needs `ollama serve`

Every backend exposes the same call:

    from llm import complete
    complete("your prompt", system="you are ...", max_tokens=2000)
"""

import json
import os
import time
import urllib.error
import urllib.request

from env import load_env

load_env()

# Per-provider default model. Override with LLM_MODEL.
DEFAULTS = {
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "cerebras": "llama-3.3-70b",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
    "deepseek": "deepseek-chat",
    "mistral": "mistral-large-latest",
    "xai": "grok-3",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "ollama": "llama3.1:8b",
}

KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "together": "TOGETHER_API_KEY",
    "ollama": None,
}

# Auto-detect order when LLM_PROVIDER is unset: free tiers first, then paid,
# then local. Whichever key is present in the environment wins.
PREFERENCE = [
    "gemini", "groq", "cerebras", "openrouter",      # free tiers
    "deepseek", "mistral", "xai", "openai", "anthropic", "together",  # paid
]


class LLMError(RuntimeError):
    pass


def _provider():
    """Which backend to use.

    Explicit LLM_PROVIDER wins. Otherwise pick the first provider in PREFERENCE
    whose key is actually set, so dropping any one API key into the environment
    is enough to make the whole agent work.
    """
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in DEFAULTS:
            raise LLMError(f"unknown LLM_PROVIDER={explicit!r}; pick one of {sorted(DEFAULTS)}")
        return explicit

    for candidate in PREFERENCE:
        if os.environ.get(KEY_ENV[candidate], "").strip():
            return candidate

    if os.environ.get("OLLAMA_HOST") or os.path.exists("/usr/local/bin/ollama"):
        return "ollama"

    raise LLMError(
        "No API key found. Set any one of these and the agent works:\n  "
        + "\n  ".join(f"{KEY_ENV[p]:<22} ({p})" for p in PREFERENCE)
        + "\n\nFree tiers: GEMINI_API_KEY (aistudio.google.com/apikey), "
        "GROQ_API_KEY (console.groq.com), CEREBRAS_API_KEY (cloud.cerebras.ai).\n"
        "Or run a local model with no key at all: `ollama serve`."
    )


def _model(provider):
    return os.environ.get("LLM_MODEL") or DEFAULTS[provider]


# Gemini's free tier is 20 requests per day PER MODEL, so a pool of models is a
# pool of independent daily quotas. Non-thinking "lite" models come first: they
# spend zero tokens on reasoning, which this extraction workload doesn't need.
GEMINI_POOL = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
]

_STATE = os.path.expanduser("~/.job-agent-state.json")


def _quota_state():
    """Which models have hit their daily cap, for today only."""
    today = time.strftime("%Y-%m-%d")
    try:
        with open(_STATE) as fh:
            state = json.load(fh)
        if state.get("date") == today:
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return {"date": today, "exhausted": []}


def _mark_exhausted(model):
    state = _quota_state()
    if model not in state["exhausted"]:
        state["exhausted"].append(model)
    try:
        with open(_STATE, "w") as fh:
            json.dump(state, fh)
    except OSError:
        pass  # losing this cache costs a wasted call, not correctness


def _models_to_try(provider):
    """Ordered models to attempt for this call.

    An explicit LLM_MODEL disables rotation — the caller asked for one model.
    Otherwise Gemini rotates through its pool, skipping anything already known
    to be capped today.
    """
    if os.environ.get("LLM_MODEL"):
        return [os.environ["LLM_MODEL"]]
    if provider != "gemini":
        return [DEFAULTS[provider]]
    spent = set(_quota_state()["exhausted"])
    fresh = [m for m in GEMINI_POOL if m not in spent]
    return fresh or GEMINI_POOL  # all capped: try anyway, the cache may be stale


def _key(provider):
    env = KEY_ENV[provider]
    if env is None:
        return None
    key = os.environ.get(env, "").strip()
    if not key:
        raise LLMError(
            f"{env} is not set. Get a free Gemini key at "
            f"https://aistudio.google.com/apikey and export it, "
            f"or set LLM_PROVIDER to another backend."
        )
    return key


def _post(url, payload, headers, timeout=120):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def complete(prompt, system=None, max_tokens=4096, temperature=0.3, retries=2):
    """Send one prompt, get the text back.

    On a daily-quota 429 the model is marked spent and the next model in the pool
    takes over immediately — no sleeping, because waiting cannot restore a daily
    cap. Transient errors (5xx, network) back off and retry on the same model.
    """
    provider = _provider()
    key = _key(provider)
    models = _models_to_try(provider)
    last_err = None

    for model in models:
        for attempt in range(retries):
            try:
                return _dispatch(
                    provider, model, key, prompt, system, max_tokens, temperature
                )
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                last_err = LLMError(f"{provider}/{model} HTTP {e.code}: {body[:200]}")
                if e.code == 429:
                    daily = "PerDay" in body or "per day" in body.lower()
                    if daily and len(models) > 1:
                        _mark_exhausted(model)
                        print(f"  [llm] {model} daily quota spent, switching model")
                        break  # next model, don't waste time sleeping
                    if attempt < retries - 1:
                        print(f"  [llm] {model} rate limited, waiting 20s")
                        time.sleep(20)
                        continue
                    break
                if e.code in (500, 502, 503, 529) and attempt < retries - 1:
                    time.sleep(4)
                    continue
                raise last_err from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = LLMError(f"{provider}/{model} unreachable: {e}")
                if attempt < retries - 1:
                    time.sleep(4)
                    continue
                break

    raise last_err or LLMError(f"{provider} failed with no usable model")


def _dispatch(provider, model, key, prompt, system, max_tokens, temperature):
    if provider == "gemini":
        # Header auth, not ?key= — the newer "AQ."-prefixed keys only work this way.
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        # Gemini 2.5+ spends maxOutputTokens on internal thinking before emitting
        # any text, so a tight budget returns an empty string rather than an
        # error. This workload is extraction and scoring, not reasoning, so
        # thinking is disabled — cheaper, faster, and it cannot eat the budget.
        #
        # Not every model accepts a zero budget: gemini-2.5-flash does,
        # gemini-flash-latest rejects it with a 400. Fall back to the smallest
        # accepted budget rather than failing.
        budget = int(os.environ.get("GEMINI_THINKING_BUDGET", "0"))
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
                "thinkingConfig": {"thinkingBudget": budget},
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        try:
            data = _post(url, payload, {"X-goog-api-key": key})
        except urllib.error.HTTPError as e:
            if e.code != 400 or budget != 0:
                raise
            payload["generationConfig"]["thinkingConfig"]["thinkingBudget"] = 128
            data = _post(url, payload, {"X-goog-api-key": key})
        candidates = data.get("candidates") or []
        if not candidates:
            blocked = data.get("promptFeedback", {}).get("blockReason")
            raise LLMError(
                f"gemini returned no candidates"
                + (f" (blocked: {blocked})" if blocked else f": {json.dumps(data)[:300]}")
            )
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            reason = candidates[0].get("finishReason", "unknown")
            raise LLMError(
                f"gemini returned no text (finishReason={reason}). "
                "If MAX_TOKENS, raise max_tokens or lower GEMINI_THINKING_BUDGET."
            )
        return text

    if provider == "anthropic":
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        data = _post(
            "https://api.anthropic.com/v1/messages",
            payload,
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        return "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()

    # Everything else speaks the OpenAI chat-completions shape.
    endpoints = {
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "cerebras": "https://api.cerebras.ai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "deepseek": "https://api.deepseek.com/v1/chat/completions",
        "mistral": "https://api.mistral.ai/v1/chat/completions",
        "xai": "https://api.x.ai/v1/chat/completions",
        "openai": "https://api.openai.com/v1/chat/completions",
        "together": "https://api.together.xyz/v1/chat/completions",
        "ollama": os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        + "/v1/chat/completions",
    }
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = _post(endpoints[provider], payload, headers)
    return data["choices"][0]["message"]["content"].strip()


def complete_json(prompt, system=None, max_tokens=4096, temperature=0.1, retries=3):
    """Same as complete(), but parses the reply as JSON.

    Models like to wrap JSON in ```json fences and add a sentence of preamble,
    so we salvage the outermost brace/bracket pair rather than trusting the
    raw string.
    """
    guard = "Reply with valid JSON only. No markdown fences, no commentary."
    system = f"{system}\n\n{guard}" if system else guard

    attempts = [
        (max_tokens, system),
        # A truncated reply is the usual cause of a parse failure, so the retry
        # gets more room and an explicit nudge to keep the payload compact.
        (int(max_tokens * 1.6), f"{system}\n\nKeep every string concise so the "
                                f"JSON is complete and closed."),
    ]

    last_raw = ""
    for tokens, sys_prompt in attempts:
        raw = complete(prompt, system=sys_prompt, max_tokens=tokens,
                       temperature=temperature, retries=retries)
        last_raw = raw

        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue

    raise LLMError(f"could not parse JSON from model reply:\n{last_raw[:500]}")


if __name__ == "__main__":
    provider = _provider()
    print(f"provider = {provider}")
    print(f"model    = {_model(provider)}")
    print("--- test call ---")
    print(complete("Reply with exactly: OK", max_tokens=20))

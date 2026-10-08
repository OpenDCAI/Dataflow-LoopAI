import os
from time import sleep

try:
    import openai
    from openai import OpenAI
except ImportError as e:
    pass

from lcb_runner.lm_styles import LMStyle
from lcb_runner.runner.base_runner import BaseRunner


class OpenAIRunner(BaseRunner):
    def __init__(self, args, model):
        super().__init__(args, model)
        self.base_url = getattr(args, "vllm_base_url", None) or os.getenv(
            "VLLM_BASE_URL"
        )
        if self.base_url:
            self.api_key = (
                getattr(args, "vllm_api_key", None)
                or os.getenv("VLLM_API_KEY")
                or "EMPTY"
            )
        else:
            self.api_key = os.getenv("OPENAI_KEY")
        self._client = None

        if model.model_style == LMStyle.OpenAIReasonPreview:
            self.client_kwargs: dict[str | str] = {
                "model": args.model,
                "max_completion_tokens": 25000,
            }
        elif model.model_style == LMStyle.OpenAIReason:
            assert (
                "__" in args.model
            ), f"Model {args.model} is not a valid OpenAI Reasoning model as we require reasoning effort in model name."
            model, reasoning_effort = args.model.split("__")
            self.client_kwargs: dict[str | str] = {
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
        else:
            self.client_kwargs: dict[str | str] = {
                "model": args.model,
                "temperature": args.temperature,
                "max_tokens": args.max_tokens,
                "top_p": args.top_p,
                "frequency_penalty": 0,
                "presence_penalty": 0,
                "n": args.n,
                "timeout": args.openai_timeout,
                # "stop": args.stop, --> stop is only used for base models currently
            }
            if self.base_url:
                # vLLM's OpenAI-compatible server supports stop sequences, while
                # the hosted OpenAI chat endpoint ignores them.
                self.client_kwargs["stop"] = args.stop

            enable_thinking = getattr(args, "enable_thinking", None)
            if enable_thinking is not None:
                # Only vLLM-style servers understand chat_template_kwargs; the
                # field is left out entirely when the caller did not ask for it.
                self.client_kwargs["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": bool(enable_thinking)}
                }

    @property
    def client(self) -> "OpenAI":
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def __getstate__(self) -> dict:
        # The OpenAI client wraps an httpx.Client and cannot be pickled, while
        # `_run_single` (a bound method holding this runner) is sent to worker
        # processes when --multiprocess is used. Workers rebuild their own client.
        state = self.__dict__.copy()
        state["_client"] = None
        return state

    def _run_single(self, prompt: list[dict[str, str]], n: int = 10) -> list[str]:
        assert isinstance(prompt, list)

        if n == 0:
            print("Max retries reached. Returning empty response.")
            return []

        try:
            response = self.client.chat.completions.create(
                messages=prompt,
                **self.client_kwargs,
            )
        except (
            openai.APIError,
            openai.RateLimitError,
            openai.InternalServerError,
            openai.OpenAIError,
            openai.APIStatusError,
            openai.APITimeoutError,
            openai.InternalServerError,
            openai.APIConnectionError,
        ) as e:
            print("Exception: ", repr(e))
            print("Sleeping for 30 seconds...")
            print("Consider reducing the number of parallel processes.")
            sleep(30)
            return self._run_single(prompt, n=n - 1)
        except Exception as e:
            print(f"Failed to run the model for {prompt}!")
            print("Exception: ", repr(e))
            raise e
        return [c.message.content for c in response.choices]

import asyncio

from groq import AsyncGroq, RateLimitError


class Summarizer:
    def __init__(self) -> None:
        self.client = AsyncGroq()
        self.summaries: dict[str, str] = {}
        self.in_progress: dict[str, asyncio.Task] = {}

    def will_summary_exist(self, file_content: str) -> bool:
        return file_content in self.summaries or file_content in self.in_progress

    async def summarize(self, file_content: str, strong: bool = True) -> str:
        if file_content in self.summaries:
            summary = self.summaries[file_content]

            if strong:
                # Immediately invalidate the cache to refresh the summary for the next time
                del self.summaries[file_content]

                # Start generating a new one:
                task = asyncio.create_task(self.summarize(file_content, strong=False))

            return summary

        if file_content in self.in_progress:
            return await self.in_progress[file_content]

        task = asyncio.create_task(self._generate_game_summary(file_content))
        self.in_progress[file_content] = task
        summary = await task
        self.summaries[file_content] = summary
        del self.in_progress[file_content]
        return summary

    async def _generate_game_summary(self, file_content: str) -> str:
        prompt = (
            "Here's the prototype code for what's eventually supposed "
            f"to become a console game:\n```python\n{file_content}\n```"
        )
        messages = [
            {
                "role": "user",
                "content": "Here's the prototype code for what's eventually supposed "
                'to become a console game:\n```python\nprint("Hello, world!")\n```',
            },
            {
                "role": "user",
                "content": "Write a single mildly (not obviously) cat-themed "
                "short sentence that's "
                "either:"
                "\n- a lighthearted joke on a not-yet-done part of the game"
                "\n- a quick suggestion on what to do next"
                "\n- a witty description of the game.\n"
                "Respond with just the sentence, nothing else.",
            },
            {
                "role": "assistant",
                "content": "The game will eventually be designed to be amasing, as soon as you stops being a sleepy cat!",
            },
            {
                "role": "user",
                "content": prompt,
            },
            {
                "role": "user",
                "content": "Write a single mildly (not obviously) cat-themed "
                "short sentence that's "
                "either:"
                "\n- a lighthearted joke on a not-yet-done part of the game"
                "\n- a quick suggestion on what to do next"
                "\n- a witty description of the game.\n"
                "Respond with just the sentence, nothing else.",
            },
        ]

        try:
            completion = await self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=1,
                max_tokens=1024,
                top_p=1,
                stream=False,
                stop=None,
            )
        except RateLimitError:
            completion = await self.client.chat.completions.create(
                model="llama-3.2-3b-preview",
                messages=messages,
                temperature=1,
                max_tokens=1024,
                top_p=1,
                stream=False,
                stop=None,
            )

        return completion.choices[0].message.content

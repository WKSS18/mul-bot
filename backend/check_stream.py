"""Small opt-in paid probe for the configured Anthropic-compatible endpoint."""
import asyncio

from app.providers import ask_deepseek


async def main():
    chunks = []

    async def on_delta(chunk):
        chunks.append(chunk)

    answer = await ask_deepseek("用一句话回答：2+2是多少？", on_delta=on_delta)
    print({"stream_chunks": len(chunks), "answer_nonempty": bool(answer.strip()), "consistent": answer == "".join(chunks)})


if __name__ == "__main__":
    asyncio.run(main())

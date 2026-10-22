"""Opt-in paid test of Anthropic-compatible tool-use with DeepSeek."""
import asyncio

from app.providers import ask_deepseek
from app.tools import calculate


async def main():
    calls = []

    async def on_tool(name, arguments, tool_id):
        calls.append((name, tool_id))
        if name != "calculator":
            raise ValueError("Unexpected tool")
        return str(calculate(arguments["expression"]))

    result = await ask_deepseek(
        "请必须调用 calculator 计算 23*17，然后只回答结果。",
        system="你必须调用 calculator 工具完成计算，不要自行心算。",
        tools=["calculator"],
        on_tool=on_tool,
    )
    print({"tool_called": bool(calls), "tool_name": calls[0][0] if calls else None, "answer_nonempty": bool(result.strip()), "answer": result[:60]})


if __name__ == "__main__":
    asyncio.run(main())

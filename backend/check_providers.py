"""Optional live verification; sends one minimal paid request only when explicitly run."""
import asyncio

from app.providers import ask_deepseek, head_object, model_ready, oss_ready


async def main():
    print(f"DeepSeek configured: {model_ready()}")
    print(f"OSS configured: {oss_ready()}")
    if model_ready():
        try:
            print("DeepSeek reply:", (await ask_deepseek("只回复 OK"))[:80])
        except Exception as exc:
            print("DeepSeek check failed:", type(exc).__name__, str(exc)[:200])
    if oss_ready():
        try:
            # 不依赖现有对象，不上传文件；构造一个不存在的 key 测试服务端签名和访问。
            head_object("tongpin/probe/does-not-exist")
        except Exception as exc:
            print("OSS HEAD result:", type(exc).__name__, str(exc)[:200])


if __name__ == "__main__":
    asyncio.run(main())

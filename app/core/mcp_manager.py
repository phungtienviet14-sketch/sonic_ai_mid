import json
import os
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from app.core.config import logger

mcp_sessions = {}
mcp_tools_registry = []
exit_stack = AsyncExitStack()

async def init_mcp_clients():
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(base_dir, "mcp_servers", "mcp_config.json")
        
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        servers = config.get("mcpServers", {})
        for name, srv_conf in servers.items():
            if srv_conf.get("type") == "stdio":
                logger.info(f"⏳ [MCP] Đang nạp server: {name}...")
                
                args = srv_conf.get("args", [])
                
                # Sửa lỗi đường dẫn: Chuyển đổi args thành đường dẫn tuyệt đối để chống sai lệch CWD
                abs_args = []
                for arg in args:
                    if arg.startswith("mcp_servers/"):
                        # Biến 'mcp_servers/tên_file.py' thành đường dẫn tuyệt đối
                        abs_args.append(os.path.join(base_dir, "mcp_servers", arg.split("/")[-1]))
                    else:
                        abs_args.append(arg)
                
                params = StdioServerParameters(
                    command=srv_conf["command"],
                    args=abs_args,
                    env=os.environ.copy()
                )

                transport = await exit_stack.enter_async_context(stdio_client(params))
                read, write = transport
                session = await exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                mcp_sessions[name] = session
                logger.info(f"✅ [MCP] Đã kết nối server: {name}")

                tools_res = await session.list_tools()
                for t in tools_res.tools:
                    mcp_tools_registry.append({"server": name, "tool": t})
                    logger.info(f"   🔧 Nạp công cụ: {t.name}")

    except Exception as e:
        logger.exception("❌ [MCP] Lỗi nghiêm trọng khi khởi tạo MCP Client:")

async def close_mcp_clients():
    await exit_stack.aclose()

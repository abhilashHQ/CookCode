from mcp.server.mcpserver import MCPServer

server = MCPServer("calc")


@server.tool()
def add(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


@server.tool()
def explode() -> str:
    """Always fails."""
    raise ValueError("boom")


if __name__ == "__main__":
    server.run("stdio")

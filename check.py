from fastmcp import FastMCP
m = FastMCP("test")
print([x for x in dir(m) if not x.startswith("_")])

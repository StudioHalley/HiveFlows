import sys
from tools.web.fetch_url import FetchUrlTool
from tools.web.url2markdown import URL2MarkdownTool
from tools.web.get_weather import GetWeatherTool
#tool = FetchUrlTool()
tool = GetWeatherTool()



output =  tool.run_from_dict({"location": "g6j1j1"})

# --- 1. Appel direct (automatisme / script) ---
#output = tool.run_from_dict({
#    "url": "https://simonwillison.net/2026/Mar/19/openai-acquiring-astral/",
#    "output_dir": "/tmp/md"
#})
print(output.data["weather"])   # /tmp/pages/3f2a1b4c.html

# --- 2. Depuis JSON string (ex: webhook, message queue) ---
#  output = tool.run_from_json('{"url": "https://example.com"}')
#
# --- 3. Intégré à LangChain ---
#   lc_tool = tool.as_langchain_tool()
#   agent = create_react_agent(llm, tools=[lc_tool], ...)


print("Got arguments: ", sys.argv)


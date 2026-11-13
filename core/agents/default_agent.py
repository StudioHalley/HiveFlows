import logging

from langchain_openai import ChatOpenAI
from langgraph.types import Checkpointer

from deepagents import create_deep_agent

from agents.skill_loader import load_skills, build_skills_prompt
from agents.tools import run_script, run_skill_script
from config import settings

logger = logging.getLogger("hiveflows")

BASE_PROMPT = open("agents/prompts/default.txt").read()


def build_default_agent(checkpointer: Checkpointer):
    """Build the default HiveFlows agent with OpenRouter LLM."""
    llm = ChatOpenAI(
        model=settings.default_llm_model,
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    skills = load_skills()
    skills_prompt = build_skills_prompt(skills)
    system_prompt = BASE_PROMPT + skills_prompt

    tools = [run_script, run_skill_script] if skills else [run_script]
    logger.info(f"Agent built with {len(skills)} skill(s), {len(tools)} tool(s)")

    return create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )

"""Nous MCP prompts: guided flows for reflection and analysis.

Unlike tools (actions) and resources (data), prompts are instruction
templates the client injects into its conversation to orchestrate a
coherent use of 4D memory. Each prompt states which resources to read
and which tools to invoke, in order.

The Spanish-named prompts below are deprecated aliases kept for
backward compatibility — they delegate to the canonical English ones.
"""

from mcp.server.fastmcp.prompts import base
from src.mcp.server import mcp_server


@mcp_server.prompt(name="daily_reflection")
def daily_reflection() -> list[base.Message]:
    """Guides a structured reflection on the day's activity.

    USE WHEN: the user asks to close their day, do a check-in or
    review how today went. Orchestrates read-only resources plus one
    optional write at the end.

    Returns:
        Message with step-by-step instructions for the agent.
    """
    return [
        base.UserMessage(
            "Guide a daily reflection using Nous memory:\n"
            "1. Read the resource memory://summary/daily for today's "
            "activity by dimension.\n"
            "2. Read memory://patterns/flow and mention only the "
            "active trajectories relevant to today.\n"
            "3. Summarize in 3 lines: what dominated (cognitive/"
            "somatic/behavioral), what was missing, and the highest "
            "amplitude event.\n"
            "4. Ask the user ONE reflection question about their day.\n"
            "5. Only if the user answers something new, store it with "
            "the remember tool (do not duplicate what is already "
            "recorded)."
        )
    ]


@mcp_server.prompt(name="weekly_review")
def weekly_review() -> list[base.Message]:
    """Guides a pattern analysis over the last week.

    USE WHEN: the user asks for a weekly retrospective or wants to
    understand recent patterns. This is analysis, not just summary:
    it must cross dimensions and detect correlations.

    Returns:
        Message with step-by-step instructions for the agent.
    """
    return [
        base.UserMessage(
            "Run a weekly review using Nous memory:\n"
            "1. Read memory://summary/weekly for the per-dimension "
            "aggregates.\n"
            "2. Read memory://patterns/flow for the last 30 days' "
            "streaks.\n"
            "3. Read memory://projection/current and use it as a "
            "starting point (it is an extrapolation, not a certainty).\n"
            "4. Identify: the dominant dimension, repeating streaks, "
            "and possible correlations (e.g. somatic spikes after "
            "behavioral streaks).\n"
            "5. Close with 2 actionable observations. If the user "
            "wants to extrapolate, use the project_trajectory tool."
        )
    ]


@mcp_server.prompt(name="prepare_decision")
def prepare_decision(topic: str) -> list[base.Message]:
    """Guides context recovery before making a decision.

    USE WHEN: the user is about to decide something and wants to
    consult their relevant memory first. Requires the agent to search
    with the recall tool, not just read resources.

    Args:
        topic: Decision topic (e.g. "switching stacks").

    Returns:
        Message with step-by-step instructions for the agent.
    """
    return [
        base.UserMessage(
            f"Prepare the context to decide about: {topic}\n"
            f"1. Invoke the recall tool with '{topic}' and top_n=5 "
            "to bring relevant memories.\n"
            "2. For each result, consider the temporal_context: what "
            "happened before and after that memory.\n"
            "3. If the search returns little, reformulate the topic "
            "with synonyms and query once more.\n"
            "4. Summarize: what I thought/decided before about this, "
            "how I felt (somatic dimension) and what I did "
            "(behavioral).\n"
            "5. DO NOT decide for the user: present the context and "
            "end with a question that clarifies the trade-off."
        )
    ]


@mcp_server.prompt(name="evaluate_decision")
def evaluate_decision(dilemma: str, options: str = "") -> list[base.Message]:
    """Evaluates a dilemma with Nous' prescriptive advisor (Pro).

    USE WHEN: the user asks for help deciding between concrete options
    and wants the calibrated verdict fed by their universe line.
    Orchestrates context + ``ask_decision`` and sets how to present
    the result without usurping the decision.

    Args:
        dilemma: The posed decision ("should I take project X?").
        options: Comma-separated options; empty = yes/no.

    Returns:
        Message with step-by-step instructions for the agent.
    """
    option_step = (
        f"the options: {options}"
        if options
        else "the default options ['yes', 'no'] — ask the user if "
        "there are concrete options they did not mention"
    )
    return [
        base.UserMessage(
            f"Evaluate the dilemma with Nous' 4D memory: {dilemma}\n"
            f"1. Invoke the ask_decision tool with the dilemma and "
            f"{option_step}.\n"
            "2. If it returns license_required/402, explain it is a "
            "Pro feature and still offer context: invoke recall with "
            "the dilemma (top_n=5) and open_commitments() for the "
            "loops that weigh.\n"
            "3. If available=false (no provider), the dict carries the "
            "assembled context — use it to reason and say so.\n"
            "4. With a verdict: present recommendation + probability "
            "+ distribution per option, mention hidden_risk when "
            ">0.5 and the verdict confidence.\n"
            "5. The model advises with data; the user decides — close "
            "asking which factor weighs most for them."
        )
    ]


# ---------------------------------------------------------------------
# Deprecated Spanish aliases — same prompts, legacy names/arguments.
# ---------------------------------------------------------------------


@mcp_server.prompt(name="reflexion_diaria")
def reflexion_diaria() -> list[base.Message]:
    """Deprecated alias — use ``daily_reflection``."""
    mensajes: list[base.Message] = daily_reflection()
    return mensajes


@mcp_server.prompt(name="analisis_semanal")
def analisis_semanal() -> list[base.Message]:
    """Deprecated alias — use ``weekly_review``."""
    mensajes: list[base.Message] = weekly_review()
    return mensajes


@mcp_server.prompt(name="preparar_decision")
def preparar_decision(tema: str) -> list[base.Message]:
    """Deprecated alias — use ``prepare_decision`` (``topic``)."""
    mensajes: list[base.Message] = prepare_decision(tema)
    return mensajes


@mcp_server.prompt(name="evaluar_decision")
def evaluar_decision(dilema: str, opciones: str = "") -> list[base.Message]:
    """Deprecated alias — use ``evaluate_decision``."""
    mensajes: list[base.Message] = evaluate_decision(dilema, opciones)
    return mensajes

"""Debate engine — orchestrates multi-agent adversarial debate (Researchers vs Brainstormers + Mediator)."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from orchestrator.agents import AgentInvocation, AgentResult, invoke_agent, invoke_agents_parallel
from orchestrator.models import (
    DebateConclusion,
    DebateConfig,
    DebatePosition,
    DebateRound,
    DebateState,
    ModelTier,
    OrchestratorConfig,
)
from orchestrator.observability import RunLogger
from orchestrator.phases import (
    build_debate_critique_prompt,
    build_debate_mediation_prompt,
    build_debate_opening_prompt,
)

logger = logging.getLogger(__name__)


class DebateEngine:
    """Orchestrates multi-agent adversarial debate before the SDLC pipeline."""

    def __init__(
        self,
        config: OrchestratorConfig,
        workspace_dir: Path,
        project_root: Path,
        run_logger: RunLogger | None = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.debate_config = config.debate
        self.workspace_dir = workspace_dir
        self.project_root = project_root
        self.run_logger = run_logger
        self.dry_run = dry_run
        self.artifacts_dir = workspace_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    async def run_debate(
        self,
        feature_request: str,
        researcher_count: int | None = None,
        brainstormer_count: int | None = None,
        max_rounds: int | None = None,
        convergence_threshold: float | None = None,
    ) -> DebateConclusion:
        """Execute the full debate lifecycle.

        1. Initialize agents (N researchers + M brainstormers)
        2. Round 1: All agents produce opening positions (parallel)
        3. Rounds 2-N: Cross-critique rounds (parallel per round, sequential rounds)
        4. Convergence check after each round
        5. Mediator synthesis (single Opus call with full debate transcript)
        6. Return DebateConclusion artifact
        """
        rc = researcher_count or self.debate_config.researcher_count
        bc = brainstormer_count or self.debate_config.brainstormer_count
        mr = max_rounds or self.debate_config.max_rounds
        ct = convergence_threshold or self.debate_config.convergence_threshold

        debate_state = DebateState(
            debate_id=uuid.uuid4().hex[:12],
            feature_request=feature_request,
            researcher_count=rc,
            brainstormer_count=bc,
            max_rounds=mr,
            status="in_progress",
        )

        # Build agent roster
        agents = []
        for i in range(1, rc + 1):
            agents.append({"agent_id": f"researcher-{i}", "agent_type": "deep_researcher"})
        for i in range(1, bc + 1):
            agents.append({"agent_id": f"brainstormer-{i}", "agent_type": "brainstormer"})

        logger.info(
            f"=== Debate Phase: {rc} researchers + {bc} brainstormers, "
            f"max {mr} rounds, convergence threshold {ct} ==="
        )

        if self.run_logger:
            self.run_logger.log_event("debate_start", {
                "debate_id": debate_state.debate_id,
                "researcher_count": rc,
                "brainstormer_count": bc,
                "max_rounds": mr,
                "convergence_threshold": ct,
            })

        # Round 1: Opening positions
        round1 = await self._run_opening_round(feature_request, agents, debate_state)
        debate_state.rounds.append(round1)
        self._save_debate_state(debate_state)

        # Rounds 2+: Critique rounds
        for round_num in range(2, mr + 1):
            # Convergence check on previous round
            if len(debate_state.rounds) >= 2:
                score = self._check_convergence(
                    debate_state.rounds[-1], debate_state.rounds[-2]
                )
                debate_state.rounds[-1].convergence_score = score
                logger.info(f"Convergence score after round {round_num - 1}: {score:.2f}")

                if score >= ct:
                    logger.info(f"Convergence reached ({score:.2f} >= {ct}), skipping to mediation")
                    debate_state.status = "converged"
                    break

            # Budget check
            if self.run_logger and self.config.max_budget_usd:
                budget_status = self.run_logger.check_budget(self.config.max_budget_usd)
                if budget_status == "exceeded":
                    logger.warning("Budget exceeded during debate, proceeding to mediation")
                    debate_state.status = "interrupted"
                    break

            round_n = await self._run_critique_round(
                round_num, debate_state.rounds, agents, feature_request, debate_state
            )
            debate_state.rounds.append(round_n)
            self._save_debate_state(debate_state)

        if debate_state.status == "in_progress":
            debate_state.status = "max_rounds_reached"

        # Final convergence score
        if len(debate_state.rounds) >= 2:
            final_score = self._check_convergence(
                debate_state.rounds[-1], debate_state.rounds[-2]
            )
            debate_state.rounds[-1].convergence_score = final_score

        # Mediation
        conclusion = await self._run_mediation(debate_state, feature_request)
        debate_state.conclusion = conclusion
        debate_state.status = "completed"
        self._save_debate_state(debate_state)

        if self.run_logger:
            self.run_logger.log_event("debate_complete", {
                "debate_id": debate_state.debate_id,
                "rounds_conducted": len(debate_state.rounds),
                "total_cost_usd": debate_state.total_cost_usd,
                "overall_confidence": conclusion.overall_confidence,
                "status": debate_state.status,
            })

        return conclusion

    async def _run_opening_round(
        self,
        feature_request: str,
        agents: list[dict[str, str]],
        debate_state: DebateState,
    ) -> DebateRound:
        """All agents produce initial positions in parallel."""
        logger.info("--- Debate Round 1: Opening Positions ---")

        if self.run_logger:
            self.run_logger.log_event("debate_round_start", {
                "debate_id": debate_state.debate_id,
                "round_number": 1,
                "agent_count": len(agents),
            })

        if self.dry_run:
            logger.info(f"[DRY RUN] Would invoke {len(agents)} agents for opening positions")
            return DebateRound(
                round_number=1,
                positions=[
                    DebatePosition(
                        agent_id=a["agent_id"],
                        agent_role=a["agent_type"],
                        round_number=1,
                        thesis="[DRY RUN] Placeholder thesis for testing purposes. " * 3,
                        evidence=["[DRY RUN] Placeholder evidence"],
                        recommendations=["[DRY RUN] Placeholder recommendation"],
                        confidence=50,
                    )
                    for a in agents
                ],
            )

        invocations = []
        for agent in agents:
            prompt = build_debate_opening_prompt(
                feature_request=feature_request,
                agent_id=agent["agent_id"],
                agent_type=agent["agent_type"],
                artifacts_dir=self.artifacts_dir,
            )
            model = (
                self.debate_config.researcher_model
                if agent["agent_type"] == "deep_researcher"
                else self.debate_config.brainstormer_model
            )
            max_turns = (
                self.debate_config.researcher_max_turns
                if agent["agent_type"] == "deep_researcher"
                else self.debate_config.brainstormer_max_turns
            )
            invocations.append(AgentInvocation(
                agent_name=agent["agent_type"],
                prompt=prompt,
                model=model,
                max_turns=max_turns,
                workspace_dir=str(self.workspace_dir),
                project_root=str(self.project_root),
                display_name=agent["agent_id"],
            ))

        results = await invoke_agents_parallel(
            invocations, max_concurrent=self.config.max_concurrent_agents,
        )

        positions = []
        round_cost = 0.0
        for agent, result in zip(agents, results):
            debate_state.total_cost_usd += result.cost_usd
            round_cost += result.cost_usd
            if self.run_logger:
                self.run_logger.log_event("agent_result", {
                    "agent": agent["agent_id"],
                    "model": self.debate_config.researcher_model.value
                    if agent["agent_type"] == "deep_researcher"
                    else self.debate_config.brainstormer_model.value,
                    "success": result.success,
                    "cost_usd": result.cost_usd,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "phase": "debate",
                    "round": 1,
                })
            pos = self._load_position(agent["agent_id"], 1, agent["agent_type"])
            if pos:
                positions.append(pos)
            else:
                logger.warning(f"Failed to load position from {agent['agent_id']} round 1")

        if not positions:
            raise RuntimeError("No positions produced in opening round — debate cannot continue")

        return DebateRound(round_number=1, positions=positions)

    async def _run_critique_round(
        self,
        round_number: int,
        previous_rounds: list[DebateRound],
        agents: list[dict[str, str]],
        feature_request: str,
        debate_state: DebateState,
    ) -> DebateRound:
        """Each agent critiques others and evolves their position."""
        logger.info(f"--- Debate Round {round_number}: Critique & Rebuttal ---")

        if self.run_logger:
            self.run_logger.log_event("debate_round_start", {
                "debate_id": debate_state.debate_id,
                "round_number": round_number,
                "agent_count": len(agents),
            })

        # Collect all positions from previous round
        prev_positions = [
            pos.model_dump() for pos in previous_rounds[-1].positions
        ]

        if self.dry_run:
            logger.info(f"[DRY RUN] Would invoke {len(agents)} agents for critique round {round_number}")
            return DebateRound(
                round_number=round_number,
                positions=[
                    DebatePosition(
                        agent_id=a["agent_id"],
                        agent_role=a["agent_type"],
                        round_number=round_number,
                        thesis="[DRY RUN] Evolved thesis for testing purposes. " * 3,
                        evidence=["[DRY RUN] Placeholder evidence"],
                        recommendations=["[DRY RUN] Placeholder recommendation"],
                        critiques_of_others=[],
                        confidence=60,
                        evolved_from_previous=True,
                        evolution_summary="[DRY RUN] Position evolved",
                    )
                    for a in agents
                ],
            )

        invocations = []
        for agent in agents:
            prompt = build_debate_critique_prompt(
                feature_request=feature_request,
                agent_id=agent["agent_id"],
                agent_type=agent["agent_type"],
                round_number=round_number,
                previous_positions=prev_positions,
                artifacts_dir=self.artifacts_dir,
            )
            model = (
                self.debate_config.researcher_model
                if agent["agent_type"] == "deep_researcher"
                else self.debate_config.brainstormer_model
            )
            max_turns = (
                self.debate_config.researcher_max_turns
                if agent["agent_type"] == "deep_researcher"
                else self.debate_config.brainstormer_max_turns
            )
            invocations.append(AgentInvocation(
                agent_name=agent["agent_type"],
                prompt=prompt,
                model=model,
                max_turns=max_turns,
                workspace_dir=str(self.workspace_dir),
                project_root=str(self.project_root),
                display_name=agent["agent_id"],
            ))

        results = await invoke_agents_parallel(
            invocations, max_concurrent=self.config.max_concurrent_agents,
        )

        positions = []
        for agent, result in zip(agents, results):
            debate_state.total_cost_usd += result.cost_usd
            if self.run_logger:
                self.run_logger.log_event("agent_result", {
                    "agent": agent["agent_id"],
                    "model": self.debate_config.researcher_model.value
                    if agent["agent_type"] == "deep_researcher"
                    else self.debate_config.brainstormer_model.value,
                    "success": result.success,
                    "cost_usd": result.cost_usd,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "phase": "debate",
                    "round": round_number,
                })
            pos = self._load_position(agent["agent_id"], round_number, agent["agent_type"])
            if pos:
                positions.append(pos)
            else:
                logger.warning(
                    f"Failed to load position from {agent['agent_id']} round {round_number}, "
                    f"carrying forward previous position"
                )
                # Carry forward previous position if agent failed
                for prev_pos in previous_rounds[-1].positions:
                    if prev_pos.agent_id == agent["agent_id"]:
                        positions.append(prev_pos)
                        break

        if not positions:
            raise RuntimeError(f"No positions produced in round {round_number}")

        return DebateRound(round_number=round_number, positions=positions)

    async def _run_mediation(
        self,
        debate_state: DebateState,
        feature_request: str,
    ) -> DebateConclusion:
        """Mediator synthesizes all rounds into a conclusion."""
        logger.info("--- Debate: Mediation Phase ---")

        if self.run_logger:
            self.run_logger.log_event("debate_mediation_start", {
                "debate_id": debate_state.debate_id,
                "rounds_to_synthesize": len(debate_state.rounds),
            })

        all_rounds = [
            {
                "round_number": r.round_number,
                "convergence_score": r.convergence_score,
                "positions": [p.model_dump() for p in r.positions],
            }
            for r in debate_state.rounds
        ]

        prompt = build_debate_mediation_prompt(
            feature_request=feature_request,
            all_rounds=all_rounds,
            artifacts_dir=self.artifacts_dir,
        )

        if self.dry_run:
            logger.info(f"[DRY RUN] Would invoke mediator with {len(all_rounds)} rounds")
            return DebateConclusion(
                resolved_requirements=[{
                    "requirement": "[DRY RUN] Placeholder requirement",
                    "rationale": "Placeholder rationale",
                    "source_agents": [],
                    "confidence": 50,
                }],
                recommended_scope="[DRY RUN] Placeholder scope for testing purposes only",
                recommended_priorities=["[DRY RUN] Priority 1"],
                overall_confidence=50,
                rounds_conducted=len(all_rounds),
                total_positions_evaluated=sum(len(r["positions"]) for r in all_rounds),
            )

        invocation = AgentInvocation(
            agent_name="mediator",
            prompt=prompt,
            model=self.debate_config.mediator_model,
            max_turns=self.debate_config.mediator_max_turns,
            workspace_dir=str(self.workspace_dir),
            project_root=str(self.project_root),
            display_name="mediator",
        )

        result = await invoke_agent(invocation)
        debate_state.total_cost_usd += result.cost_usd
        if self.run_logger:
            self.run_logger.log_event("agent_result", {
                "agent": "mediator",
                "model": self.debate_config.mediator_model.value,
                "success": result.success,
                "cost_usd": result.cost_usd,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "phase": "debate",
                "round": "mediation",
            })

        if not result.success:
            raise RuntimeError(f"Mediator failed: {result.error}")

        # Load and validate the conclusion
        conclusion_path = self.artifacts_dir / "debate_conclusion.json"
        if not conclusion_path.exists():
            raise RuntimeError(
                f"Mediator did not produce debate_conclusion.json at {conclusion_path}"
            )

        with open(conclusion_path) as f:
            conclusion_data = json.load(f)

        conclusion = DebateConclusion.model_validate(conclusion_data)
        return conclusion

    def _check_convergence(
        self, current: DebateRound, previous: DebateRound
    ) -> float:
        """Compute convergence score (0.0 to 1.0).

        Components (weighted average):
        - agreement_ratio (0.4): agreements / (agreements + critiques)
        - confidence_stability (0.3): 1.0 - avg(|conf_now - conf_prev|) / 100
        - evolution_rate (0.3): 1.0 - (evolved_count / total)
        """
        # Agreement ratio
        total_agreements = 0
        total_critiques = 0
        for pos in current.positions:
            total_agreements += len(pos.agreements_with_others)
            total_critiques += len(pos.critiques_of_others)

        if total_agreements + total_critiques > 0:
            agreement_ratio = total_agreements / (total_agreements + total_critiques)
        else:
            agreement_ratio = 0.5  # neutral if no interactions

        # Confidence stability
        conf_deltas = []
        for curr_pos in current.positions:
            for prev_pos in previous.positions:
                if curr_pos.agent_id == prev_pos.agent_id:
                    conf_deltas.append(abs(curr_pos.confidence - prev_pos.confidence))
                    break

        if conf_deltas:
            avg_delta = sum(conf_deltas) / len(conf_deltas)
            confidence_stability = 1.0 - (avg_delta / 100.0)
        else:
            confidence_stability = 0.5

        # Evolution rate (fewer evolutions = more convergence)
        evolved_count = sum(1 for pos in current.positions if pos.evolved_from_previous)
        total = len(current.positions) or 1
        evolution_rate = 1.0 - (evolved_count / total)

        score = (
            0.4 * agreement_ratio
            + 0.3 * confidence_stability
            + 0.3 * evolution_rate
        )
        return max(0.0, min(1.0, score))

    def _load_position(
        self, agent_id: str, round_number: int, agent_type: str
    ) -> DebatePosition | None:
        """Load a position artifact written by an agent."""
        path = self.artifacts_dir / f"debate_{agent_id}_round_{round_number}.json"
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return DebatePosition.model_validate(data)
        except Exception as e:
            logger.warning(f"Failed to parse position from {path}: {e}")
            return None

    def _save_debate_state(self, state: DebateState) -> None:
        """Persist debate state for resume capability."""
        state_path = self.workspace_dir / "debate_state.json"
        with open(state_path, "w") as f:
            json.dump(state.model_dump(), f, indent=2, default=str)

    def enrich_feature_request(
        self, feature_request: str, conclusion: DebateConclusion
    ) -> str:
        """Enrich the original feature request with debate conclusions."""
        requirements = "\n".join(
            f"- [{r.confidence}% confidence] {r.requirement} — {r.rationale}"
            for r in conclusion.resolved_requirements
        )

        risks = "\n".join(
            f"- [{r.severity}] {r.risk} — Mitigation: {r.mitigation}"
            for r in conclusion.risk_assessment
        ) if conclusion.risk_assessment else "No significant risks identified."

        tensions = "\n".join(
            f"- {t.tension}: {t.mediator_recommendation}"
            for t in conclusion.unresolved_tensions
        ) if conclusion.unresolved_tensions else "No unresolved tensions."

        priorities = "\n".join(
            f"{i}. {p}" for i, p in enumerate(conclusion.recommended_priorities, 1)
        )

        dissent = "\n".join(
            f"- {d.agent_id}: {d.dissent} (Note: {d.mediator_note})"
            for d in conclusion.dissenting_opinions
        ) if conclusion.dissenting_opinions else "None."

        return f"""{feature_request}

---

## Debate Phase Conclusions (Confidence: {conclusion.overall_confidence}%)

The following conclusions emerged from a structured adversarial debate between {conclusion.rounds_conducted} round(s) of Deep Researchers and Brainstormers, synthesized by a Mediator.

### Resolved Requirements
{requirements}

### Recommended Scope
{conclusion.recommended_scope}

### Priorities
{priorities}

### Risk Assessment
{risks}

### Unresolved Tensions (for human review)
{tensions}

### Dissenting Opinions Worth Considering
{dissent}
"""

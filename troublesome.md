I am getting all these errors - ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-03-23T13:07:28.298572Z [info     ] run_complete                   phases={'competitor_research': {'status': <PhaseStatus.FAILED: 'failed'>, 'retry_count': 0, 'model_tier': <ModelTier.SONNET: 'sonnet'>, 'cost_usd': 0.0, 'error': 'Artifact validation: {\'competitor_research\': ["Schema: \'summary\' is a required property", \'Model: Field required (at summary)\']}', 'error_code': None, 'artifact_retry_exhausted': True}} total_cost_usd=2.3112647500000003 workflow_type=custom
Cleaned up MCP config from /Users/amangupta/Projects/layersiq/.mcp.json
Cleaned up test-runner MCP config from /Users/amangupta/Projects/layersiq/.mcp.json
                         Run 432deb78c554 — I have noticed there are several gaps -                         
                                             1.⁠ ⁠Most Of The li                                              
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Phase / Step        ┃ Status ┃ Cost (USD) ┃ Error                                                        ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ competitor_research │ failed │          - │ Artifact validation: {'competitor_research': ["Schema: 'summ │
└─────────────────────┴────────┴────────────┴──────────────────────────────────────────────────────────────┘
                                                   Agents Deployed                                                    
┏━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ #   ┃ Agent                 ┃ Specialty                                                              ┃ Invocations ┃
┡━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 1   │ Competitor Researcher │ Competitive landscape analysis, feature benchmarking, positioning, dif │           1 │
└─────┴───────────────────────┴────────────────────────────────────────────────────────────────────────┴─────────────┘
                                   Task Timeline                                    
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Task                              ┃ Agent Role            ┃ Duration ┃ Status    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━┩
│ Execute step: Competitor Research │ Competitor Researcher │    8m 2s │ completed │
│                                   │                       │          │           │
│ --- ARTIFACT ER                   │                       │          │           │
└───────────────────────────────────┴───────────────────────┴──────────┴───────────┘
╭─────────────────────────────────────────────────────────────────────────────────────────────────────────────── Run Summary ────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ Workflow: custom | Steps: 0/1 completed (1 failed)                                                                                                                                                                                         │
│ Agents: 1 unique, 1 total invocations | Total cost: $2.3113 | Wall clock: 8m 2s                                                                                                                                                            │
│ Review cycles: 0                                                                                                                                                                                                                           │
│ Artifacts: /Users/amangupta/Projects/layersiq/workspace/artifacts/                                                                                                                                                                         │
│ Log: /Users/amangupta/Projects/layersiq/workspace/logs/run-432deb78c554.jsonl                                                                                                                                                              │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
2026-03-23T13:07:28.308384Z [info     ] orchestration_complete         agents_used=[{'role': 'competitor_researcher', 'invocations': 1}] completed_phases=0 failed_phases=1 input_tokens=0 output_tokens=0 review_cycles=0 run_id=432deb78c554 total_agents=1 total_cost_usd=2.3113 total_invocations=1 total_phases=1 total_tokens=0 wall_clock_seconds=482.17 workflow_type=custom
amangupta@Amans-MacBook-Pro layersiq % 


these are very troublesome, getting a lot of times, fix them permanently please
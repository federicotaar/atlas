"""Multi-Agent Engine (fase 2, spec 5D): Researcher, Planner, Critic.

Regole dalla specifica:
- scambiano dati strutturati (schemi Pydantic), non testo libero;
- ogni esecuzione ha limite di passi, timeout e budget (costo: 0, solo locale);
- ogni esecuzione e registrata in agent_runs;
- l'orchestratore decide ordine e stop; il Critic ha l'ultima parola.
"""

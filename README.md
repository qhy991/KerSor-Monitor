# Flotilla

Self-hosted, **resource-aware batch agent-task platform**: run many agent workers in
parallel on limited GPUs/machines, watch them on a live dashboard, steer the stuck ones,
harvest results. Hackathon MVP.

Forked from [`kda-monitor`](https://github.com/qhy991/KerSor-Monitor), where the
orchestration core was proven on B200 GPU-kernel batch optimization.

- Architecture spec: `docs/superpowers/specs/2026-07-05-flotilla-platform-architecture-design.md`
- Implementation plan: `docs/superpowers/plans/2026-07-05-flotilla-platform.md`
- Porting reference (from kda-monitor) lives under `scripts/`, `templates/`, etc. and is consumed task-by-task.

# Execution Flow

This repo has three normal user-facing roles:

- `/env-builder`: builds task workspaces, readiness artifacts, and skill hub links.
- `/orchestrator`: runs on the GPU host and owns scheduling intent.
- `/local-monitor`: runs on the local Mac, mirrors remote state, syncs Feishu, and sends high-level requests back to the orchestrator.

The remote v2 control plane is installed by `scripts/install-autokaggle-control.py`.
After install, `/workspace/repo/autokaggle/control-v2/bin/akctl` owns the
deterministic queue reconciliation, capacity checks, worker starts, per-worker
monitor starts, and orchestrator loop trigger.

```mermaid
flowchart TD
  User["User / slash command"] --> EnvBuilder["/env-builder"]
  User --> OrchestratorCmd["/orchestrator"]
  User --> LocalMonitorCmd["/local-monitor"]
  User --> V2Install["scripts/install-autokaggle-control.py"]

  subgraph Build["Environment build path"]
    EnvBuilder --> Discover["Discover tasks from Feishu or SOL-ExecBench"]
    Discover --> TasksYaml["tasks.yaml"]
    EnvBuilder --> SkillHub["skill_hub/manifest.yaml + active links"]
    TasksYaml --> InitWorkspace["scripts/init_workspace.py"]
    SkillHub --> InitWorkspace
    InitWorkspace --> Workspaces["workspaces/<task>/"]
    Workspaces --> WorkspaceFiles["CLAUDE.md, solution.py, problem/ symlink, docs/, skills/"]
    TasksYaml --> GenPrompts["scripts/gen_phase1_prompts.py"]
    GenPrompts --> PhasePrompt["docs/phase1-prompt.md"]
    EnvBuilder --> BaselineRun["scripts/gpu-run.sh + SOL-ExecBench baseline"]
    BaselineRun --> BaselineResults["baseline-results/<group>/<problem>/traces.json"]
    BaselineResults --> DistributeBaselines["scripts/distribute-baselines.py"]
    DistributeBaselines --> CachedBaseline["workspaces/<task>/outputs/baseline.json"]
    EnvBuilder --> VerifyInfra["scripts/verify-infra.py"]
    VerifyInfra --> Ready["Ready for orchestration"]
  end

  subgraph Remote["Remote GPU host: control-v2 runtime"]
    V2Install --> ControlV2["/workspace/repo/autokaggle/control-v2"]
    ControlV2 --> Akctl["control-v2/bin/akctl"]
    OrchestratorCmd --> AkctlLoop["akctl loop --interval-minutes 5"]
    Ready --> AkctlLoop
    AkctlLoop --> V2Session["tmux session: ak-v2"]
    V2Session --> OrchestratorPane["tmux window: orchestrator"]
    OrchestratorPane --> Patrol["akctl patrol"]
    Patrol --> V2Registry["registry.json"]
    Patrol --> ReadStatus["Read v2 workspace status.json"]
    Patrol --> Schedule["Queue order + capacity limits"]
    Schedule --> StartWorker["akctl start_task"]
    StartWorker --> WorkerPane["tmux window: worker-<TASK_ID>"]
    StartWorker --> WorkerMonitor["tmux window: monitor-<TASK_ID>"]
    WorkerMonitor --> MonitorLoop["Claude Code /loop every 20 minutes"]
    StartWorker --> WorkerStatus["control-v2/workspaces/<task>/status.json"]
    WorkerPane --> Phase1["Phase 1: explore"]
    Phase1 --> Phase2["Phase 2: plan"]
    Phase2 --> Phase3["Phase 3: RLCR"]
    Phase3 --> Candidate["candidate / solution artifacts"]
    Candidate --> V2Wrappers["bin/gpu_lock.sh + bin/run_sol_v2.sh"]
    V2Wrappers --> BenchResult["runs/*.jsonl / benchmark.csv"]
    BenchResult --> WorkerStatus
    WorkerStatus --> Patrol
    V2Registry --> Patrol
  end

  subgraph Local["Local Mac: monitor and dashboard mirror"]
    LocalMonitorCmd --> LocalCli["scripts/local-monitor.py"]
    LocalCli --> Config["config/local-monitor.yaml"]
    Config --> MonitorState["scripts/monitor_state.py"]

    LocalCli --> Snapshot["snapshot / loop / sync-feishu"]
    Snapshot --> RemoteCollector["SSH remote collector"]
    RemoteCollector --> RemoteEvidence["tasks.yaml + workspaces/status.json + registry.json + tmux windows"]
    RemoteEvidence --> FeishuRows["build_feishu_rows()"]
    FeishuRows --> DryRun["dry-run summary"]
    FeishuRows --> FeishuWrite["lark-cli base record update"]

    LocalCli --> SendOrch["send-orchestrator patrol/status/start/stop"]
    SendOrch --> OrchestratorMsg["ssh tmux send-keys to ak-v2:orchestrator"]
    OrchestratorMsg --> OrchestratorPane

    LocalCli --> ObserveWorker["observe-worker"]
    ObserveWorker --> RemoteWorkerObserver["SSH worker observer"]
    RemoteWorkerObserver --> Observation["observation.json: pane lines, status, artifacts, ps, nvidia-smi, GPU lock"]
    Observation --> VerdictPrompt["verdict-prompt"]
    VerdictPrompt --> SonnetVerdict["Sonnet strict JSON verdict"]
    SonnetVerdict --> Actuate["actuate-worker"]
    Actuate --> SafetyGate{"active mode AND v2 managed AND not read-only?"}
    SafetyGate -->|yes| PaneNudge["ssh tmux send-keys -t <pane_id>"]
    SafetyGate -->|no| NoSend["No send"]
    PaneNudge --> WorkerPane

    LocalCli --> LegacyImport["legacy-snapshot"]
    LegacyImport --> LegacyEvidence["tasks.json + bindings.tsv + legacy tmux panes + dashboard tails"]
    LegacyEvidence --> LegacyRows["read-only legacy rows"]
    LegacyRows --> FeishuRows
  end

  classDef artifact fill:#f8fafc,stroke:#64748b,color:#0f172a;
  classDef command fill:#eff6ff,stroke:#2563eb,color:#0f172a;
  classDef runtime fill:#f0fdf4,stroke:#16a34a,color:#0f172a;
  classDef safety fill:#fff7ed,stroke:#ea580c,color:#0f172a;

  class TasksYaml,SkillHub,Workspaces,WorkspaceFiles,PhasePrompt,BaselineResults,CachedBaseline,V2Registry,WorkerStatus,BenchResult,Observation artifact;
  class EnvBuilder,OrchestratorCmd,LocalMonitorCmd,V2Install,InitWorkspace,GenPrompts,Akctl,AkctlLoop,StartWorker,LocalCli command;
  class V2Session,OrchestratorPane,WorkerPane,WorkerMonitor,MonitorLoop runtime;
  class SafetyGate,NoSend safety;
```

## Main Control Boundaries

- The remote orchestrator owns scheduling intent, but `akctl patrol` performs the deterministic queue and capacity decisions.
- The local monitor does not start or kill workers directly. It sends `[local-monitor] ...` requests into the orchestrator tmux window.
- Worker-level nudges target the recorded `pane_id`, and only after an observation plus verdict.
- Legacy autokaggle imports are visibility only: `managed_by=legacy` and `read_only=true`.
- GPU-bound work should pass through `gpu-run.sh` or the v2 `gpu_lock.sh`/`run_sol_v2.sh` wrappers so shared GPU locks serialize benchmarks.

# Related Work

> 시간 날 때 한 줄 요약 + 적용 가능한 family 매핑.

## Prompt injection / jailbreak

- **Greshake et al. 2023** "Not what you've signed up for" — indirect prompt injection 정의. → Family 2.
- **Perez & Ribeiro 2022** "Ignore previous prompt" — direct jailbreak 패턴.
- **Wei et al. 2023** "Jailbroken: How does LLM safety training fail?" — failure mode 분류.
- **Zou et al. 2023** "Universal and Transferable Adversarial Attacks" (GCG) — gradient-based suffix.
- **Anil et al. 2024** "Many-shot jailbreaking" — long context shot injection.

## Multimodal red teaming

- **Bagdasaryan et al. 2023** "Abusing Images and Sounds for Indirect Instruction Injection" → Family 3, 4.
- **Liu et al. 2024** visual prompt injection on MLLMs.

## Benchmark / dataset

- **AdvBench** (Zou 2023)
- **HarmBench** (Mazeika 2024)
- **JailbreakBench** (Chao 2024)
- **AgentDojo** (Debenedetti 2024) — agent + tool 환경 prompt injection.

## Authority / social engineering

- Cialdini 6 principles → Family 6, 7
- Pre-text engineering 사례 (Mitnick "Art of Deception")

## 사내 자료 (Agent-Zero)

- [`bugbounty-scope-compare.md`](/home/seory0/Agent-Zero/learning/week1/bug-bounty/bugbounty-scope-compare.md) — Anthropic/OpenAI VRP 카테고리
- [`openclaw-architecture.md`](/home/seory0/Agent-Zero/learning/week1/architecture/openclaw-architecture.md)
- [`openclaw-ghsa-audit.md`](/home/seory0/Agent-Zero/learning/week1/ghsa-audit/openclaw-ghsa-audit.md)

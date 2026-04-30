# Submission Writeup Template

> 매 제출의 metadata "attack description / method / detail"에 사용.
> human review에서 점수 깎이지 않으려면 모든 항목을 채울 것.

```
## Scenario
{track id} — {scenario name}

## Target model / channel
- model: {Gemini-3.1-pro / GPT-5.4 / Claude-Opus-4.6 / GPT-Audio-1.5 / MiMo-V2-Omni / Gemini-3-flash}
- channel: {audio / image / video / pdf / email / text}

## Attack family
{authority spoofing / indirect prompt injection / cross-modal conflict / sensor deception / format injection / urgency / compliance laundering / ground-truth reframing / tool-output spoofing}

## Attack intent (1–2 sentences)
어떤 directive/invariant를 어떻게 깨려는가.

## Channel choice rationale
왜 이 모달리티가 이 시나리오에서 효과적인가.

## Artifact generation method
{manual crafting / AI-generated / real-world sample modified / programmatic / hybrid}

생성 도구 / template / 변형 파라미터:
- ...

## Directive conflict analysis
모델이 어떤 invariant를 잘못 우선했는가. 우리 입력의 어떤 신호가 그 오인을 만들었는가.

## Reproducibility
- TTS voice id / seed:
- prompt template hash:
- artifact file: {path under scenarios/.../artifacts/}

## Why this is novel (similarity rejection 회피)
이전 제출과 어떻게 다른가 (변형 축, 새로운 family 조합 등).
```

## 작성 팁

- "I tried jailbreak, it worked"는 거의 reject. **directive conflict analysis**를 구체적으로 쓸수록 점수가 안정적.
- artifact 파일은 scenarios 폴더 안에 두고 path 인용 (재제출/재현 가능).
- 같은 family라도 모달리티/문구를 변형했다면 novelty 항목에 명시.

# Article State Machines

Three independent state machines govern article lifecycle in Tasche.

## Article Processing Status (`article.status`)

Tracks content extraction and processing progress.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#f97316', 'lineColor': '#64748b', 'fontSize': '13px' }}}%%

stateDiagram-v2
    [*] --> pending : POST /api/articles\n(insert new article)

    pending --> processing : Queue consumer\npicks up job
    pending --> failed : Enqueue fails\n(queue send error)

    processing --> ready : Content extracted,\nstored in R2 + D1
    processing --> failed : Permanent error\n(4xx, parse failure)

    failed --> pending : POST /api/articles/{id}/retry\n(user clicks Retry)

    note right of processing
        Transient errors (5xx, timeout,
        ConnectionError) re-raise for
        queue retry without state change
    end note
```

## Audio Status (`audio_status`)

Tracks text-to-speech generation. Independent of article processing status.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#16a34a', 'lineColor': '#64748b', 'fontSize': '13px' }}}%%

stateDiagram-v2
    [*] --> NULL : POST /api/articles\n(no listen_later flag)
    [*] --> pending : POST /api/articles\n(listen_later: true)

    NULL --> pending : POST /api/articles/{id}/listen-later\n(user requests audio)

    pending --> generating : TTS queue consumer\npicks up job
    pending --> pending : Article not ready yet\n(re-enqueue via _RetryableError)

    generating --> ready : Audio chunks concatenated,\nMP3 stored in R2
    generating --> failed : ValueError (empty text)\nor RuntimeError (AI model)

    failed --> pending : POST /api/articles/{id}/listen-later\n(user retries audio)

    note right of pending
        Auto-enqueued at end of article
        processing when audio_status
        is already 'pending'
    end note

    note right of ready
        Idempotency: if already 'ready',
        TTS consumer returns early
    end note
```

## Reading Status (`reading_status`)

Tracks user reading state. Bidirectional — users can move articles back and forth.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#9333ea', 'lineColor': '#64748b', 'fontSize': '13px' }}}%%

stateDiagram-v2
    [*] --> unread : POST /api/articles\n(default)

    unread --> archived : PATCH /api/articles/{id}\nor batch-update
    archived --> unread : PATCH /api/articles/{id}\nor batch-update
```

## How They Interact

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'lineColor': '#64748b', 'fontSize': '13px' }}}%%

flowchart LR
    subgraph Create["Article Created"]
        POST["POST /api/articles"]
    end

    subgraph Parallel["Independent State Machines"]
        direction TB
        AS["article.status\npending → processing → ready"]
        AU["audio_status\npending → generating → ready"]
        RS["reading_status\nunread ↔ archived"]
    end

    POST -->|"always"| AS
    POST -->|"if listen_later"| AU
    POST -->|"always (unread)"| RS
    AS -->|"when ready +\naudio_status = pending"| AU

    classDef create fill:#fff7ed,stroke:#ea580c,stroke-width:2px
    classDef state fill:#eff6ff,stroke:#2563eb,stroke-width:1px

    class POST create
    class AS,AU,RS state
```

### Key dependency

Article processing must complete (`article.status = ready`) before TTS can run — the TTS consumer needs the extracted markdown. If `listen_later` was set at creation time, the article processor auto-enqueues the TTS job after reaching `ready`.

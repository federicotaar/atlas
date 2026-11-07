import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # chat | file | connector | cli ...
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    start_date: Mapped[date | None] = mapped_column(nullable=True)
    target_date: Mapped[date | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Event(Base):
    """Log append-only: niente update, niente delete (cancellazione solo via diritto all'oblio)."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_event_at", "user_id", "event_at"),
        Index("ix_events_user_project_event_at", "user_id", "project_id", "event_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("sources.id"), nullable=True)
    event_at: Mapped[datetime] = mapped_column(nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_user_recorded_at", "user_id", "recorded_at"),
        Index("ix_memories_user_kind", "user_id", "kind"),
        CheckConstraint("kind IN ('fact','opinion','decision','hypothesis','task')", name="memories_kind_check"),
        CheckConstraint("origin IN ('stated','inferred','imported')", name="memories_origin_check"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="memories_confidence_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("sources.id"), nullable=False)
    event_at: Mapped[datetime | None] = mapped_column(nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    confidence: Mapped[float] = mapped_column(nullable=False)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memories.id"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="private")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)

    source: Mapped[Source] = relationship("Source", lazy="joined")


class ActionLog(Base):
    """Storico operazioni e accessi. Mai contenuti personali: solo tipo, id e metadati."""

    __tablename__ = "action_logs"
    __table_args__ = (
        Index("ix_action_logs_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Entity(Base):
    """Knowledge Graph fase 1 (spec 5C): persone, luoghi, progetti, concetti.
    L'estrazione automatica crea proposte 'pending': diventano reali solo con
    conferma esplicita (spec: 'parte come proposta da confermare')."""

    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint("kind IN ('person','place','project','concept')", name="entities_kind_check"),
        CheckConstraint("status IN ('pending','confirmed','rejected')", name="entities_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    memory_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("memories.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        CheckConstraint("status IN ('pending','confirmed','rejected')", name="relationships_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    from_entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("entities.id"), nullable=False)
    to_entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("entities.id"), nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    source_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("sources.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    from_entity: Mapped[Entity] = relationship("Entity", foreign_keys=[from_entity_id], lazy="joined")
    to_entity: Mapped[Entity] = relationship("Entity", foreign_keys=[to_entity_id], lazy="joined")


class Conversation(Base):
    """Conversazioni salvate (fase 1): ogni scambio /ask persiste i due turni."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="messages_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class AgentRun(Base):
    """Registro esecuzioni agenti (fase 2, spec 5D): input, output, passi,
    durata, costo (sempre 0: solo provider locali), esito."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_user_created", "user_id", "created_at"),
        CheckConstraint("status IN ('ok','error','stopped')", name="agent_runs_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    steps: Mapped[int] = mapped_column(nullable=False, default=1)
    duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_eur: Mapped[float] = mapped_column(nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Prediction(Base):
    """Prediction Engine (fase 3, spec 5E): ogni previsione registra domanda,
    stima centrale, intervallo, probabilita dichiarata, dati usati, assunzioni,
    fattori di rischio e data di verifica. Alla verifica si confronta con
    l'esito reale e si calcola l'errore."""

    __tablename__ = "predictions"
    __table_args__ = (
        Index("ix_predictions_user_status_verify", "user_id", "status", "verify_at"),
        CheckConstraint("status IN ('open','verified','refused')", name="predictions_status_check"),
        CheckConstraint("probability IS NULL OR (probability >= 0 AND probability <= 1)",
                        name="predictions_probability_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    estimate_central: Mapped[float | None] = mapped_column(nullable=True)
    estimate_low: Mapped[float | None] = mapped_column(nullable=True)
    estimate_high: Mapped[float | None] = mapped_column(nullable=True)
    unit: Mapped[str] = mapped_column(Text, nullable=False, default="")
    probability: Mapped[float | None] = mapped_column(nullable=True)
    data_used: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    assumptions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    risk_factors: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verify_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    outcome: Mapped[float | None] = mapped_column(nullable=True)
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[float | None] = mapped_column(nullable=True)
    brier: Mapped[float | None] = mapped_column(nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


GOOGLE_CONNECTOR_KINDS = ("google_calendar", "google_gmail")


class Connector(Base):
    """Connettore (fase 4, spec 5F): permesso una volta per connettore.
    Fittizi: calendar_mock ed email_mock (dati sintetici). Reali:
    google_calendar (sola lettura) e google_gmail (solo bozze, mai invii),
    autorizzati via OAuth; i token vivono cifrati in connector_credentials."""

    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('calendar_mock','email_mock','google_calendar','google_gmail')",
            name="connectors_kind_check"),
        CheckConstraint("status IN ('pending','authorized','revoked')", name="connectors_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    authorized_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class EngineAction(Base):
    """Action Engine (fase 4, spec 5F): ogni azione ha livello, anteprima
    esatta, conferma esplicita per il livello 2, esito registrato,
    annullamento quando possibile e chiave di idempotenza."""

    __tablename__ = "engine_actions"
    __table_args__ = (
        Index("ix_engine_actions_user_status", "user_id", "status"),
        CheckConstraint("level IN (0,1,2)", name="engine_actions_level_check"),
        CheckConstraint(
            "status IN ('previewed','pending_confirm','executed','failed','cancelled')",
            name="engine_actions_status_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True),
                                                           ForeignKey("connectors.id"), nullable=True)
    level: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="previewed")
    preview: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MockCalendarEvent(Base):
    """Eventi fittizi del connettore calendar_mock: dati sintetici, mai un
    calendario reale."""

    __tablename__ = "mock_calendar_events"
    __table_args__ = (Index("ix_mock_calendar_user", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(nullable=False)
    ends_at: Mapped[datetime] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="fixture")  # fixture | created
    cancelled: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_action_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class MockEmailDraft(Base):
    """Bozze fittizie del connettore email_mock (fase 4): una bozza non e un
    invio. Non esiste alcuna operazione di invio."""

    __tablename__ = "mock_email_drafts"
    __table_args__ = (Index("ix_mock_email_user", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    to_addr: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")  # draft | discarded
    created_action_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class ConnectorCredential(Base):
    """Token OAuth di un connettore reale, cifrati con Fernet
    (ATLAS_CREDENTIALS_KEY). Mai in chiaro, mai nei log."""

    __tablename__ = "connector_credentials"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("connectors.id"), nullable=False, unique=True)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(),
                                                 onupdate=func.now())

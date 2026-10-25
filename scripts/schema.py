"""Export fixed DTOs without initializing a database or resolving credentials."""

import json
from pathlib import Path

from enloq.api.accounts import (
    AccountProcessing,
    Accounts,
    AccountSummary,
    HideBots,
    LoginState,
    ReadingView,
    Settings,
)
from enloq.api.auth import (
    Agreement,
    AgreementAcceptance,
    BrowserStatus,
    ClientDecision,
    CodeAttempt,
    CodeEntry,
    CodeStart,
    DeviceClaim,
    DeviceClaimStatus,
    DeviceCredential,
    PairingStatus,
    SignInPairingState,
    SignInStart,
    TrustedBrowser,
)
from enloq.api.deletions import DeletionSubmit, DeletionTarget
from enloq.api.memory import AccountMemoryView, MemoryView, Remembering
from enloq.api.notifications import (
    DeviceNotificationStatus,
    DevicePreferences,
    DeviceToken,
    NotificationAttentionStatus,
    NotificationPreferences,
    NotificationSoundUpload,
    NotificationStatus,
    NotificationTarget,
)
from enloq.api.people import (
    Confirmation,
    JoinRequest,
    Me,
    MemberBudgetEdit,
    MemberBudgets,
    People,
    Person,
    Removed,
)
from enloq.api.routes import (
    BlockRetry,
    CachedOriginalsRead,
    Calming,
    CalmingEdit,
    CorrespondentProfile,
    GroupProcessingEdit,
    GroupProfile,
    MemberProfile,
    PreparedVideo,
    SuggestionSettings,
    VideoPreparation,
)
from enloq.api.traits import ScaleEdit, TraitsView
from enloq.application.previews import ConversationPreview, ConversationRow
from enloq.domain.catalog import (
    CatalogItem,
    CatalogMetadata,
    CatalogOpen,
    CatalogPack,
    CatalogPackView,
    CatalogPreview,
    CatalogRefresh,
    CatalogView,
    GifSearch,
    GifSearchScope,
    GifSearchView,
)
from enloq.domain.catalog_assets import (
    AssetRepresentation,
    CatalogAssetPreparation,
    CatalogAssetView,
    ReceivedAssetPreparation,
    ReceivedAssetView,
)
from enloq.domain.emotion import EmotionView
from enloq.domain.models import Chat, GroupProcessing, Message
from enloq.domain.reminders import (
    DigestSettings,
    ReminderAction,
    ReminderEdit,
    ReminderList,
    ReminderPreview,
    ReminderReview,
    ReminderRevision,
    ReminderView,
)
from enloq.domain.selected_media import (
    SelectedMediaAction,
    SelectedMediaView,
    SelectMedia,
)
from enloq.domain.suggestions import SuggestionsView
from enloq.storage.reactions import ReactionView
from enloq.storage.viewing import ViewedContent, ViewedSources
from pydantic.json_schema import models_json_schema

_, schema = models_json_schema(
    [
        # Served shapes: Message omits reaction evidence, which browsers
        # receive only as its ReactionView projection.
        (m, "serialization" if m in (ConversationRow, Message) else "validation")
        for m in (
            Chat,
            DigestSettings,
            ReminderAction,
            ReminderEdit,
            ReminderList,
            ReminderPreview,
            ReminderReview,
            ReminderRevision,
            ReminderView,
            Message,
            AccountSummary,
            AccountProcessing,
            ReadingView,
            HideBots,
            Settings,
            Accounts,
            LoginState,
            BrowserStatus,
            PairingStatus,
            DeviceClaim,
            DeviceClaimStatus,
            DeviceCredential,
            CodeStart,
            CodeAttempt,
            CodeEntry,
            ClientDecision,
            SignInStart,
            SignInPairingState,
            Agreement,
            AgreementAcceptance,
            Me,
            JoinRequest,
            Person,
            People,
            MemberBudgets,
            MemberBudgetEdit,
            Confirmation,
            Removed,
            TrustedBrowser,
            NotificationStatus,
            NotificationTarget,
            NotificationAttentionStatus,
            NotificationPreferences,
            NotificationSoundUpload,
            DeviceToken,
            DeviceNotificationStatus,
            DevicePreferences,
            MemoryView,
            AccountMemoryView,
            Remembering,
            TraitsView,
            ScaleEdit,
            EmotionView,
            SuggestionsView,
            SuggestionSettings,
            ViewedContent,
            ViewedSources,
            ReactionView,
            ConversationPreview,
            ConversationRow,
            DeletionTarget,
            DeletionSubmit,
            BlockRetry,
            CachedOriginalsRead,
            CorrespondentProfile,
            GroupProfile,
            MemberProfile,
            GroupProcessing,
            GroupProcessingEdit,
            Calming,
            CalmingEdit,
            VideoPreparation,
            PreparedVideo,
            CatalogMetadata,
            CatalogPreview,
            CatalogItem,
            CatalogPack,
            CatalogView,
            CatalogPackView,
            CatalogRefresh,
            CatalogOpen,
            GifSearch,
            GifSearchScope,
            GifSearchView,
            AssetRepresentation,
            CatalogAssetView,
            CatalogAssetPreparation,
            ReceivedAssetPreparation,
            ReceivedAssetView,
            SelectedMediaView,
            SelectMedia,
            SelectedMediaAction,
        )
    ],
    ref_template="#/components/schemas/{model}",
)
document = {
    "openapi": "3.1.0",
    "info": {"title": "Enloq DTOs", "version": "1"},
    "paths": {},
    "components": {"schemas": schema["$defs"]},
}
Path("config/api-schema.json").write_text(
    json.dumps(document, ensure_ascii=False, indent=2) + "\n"
)

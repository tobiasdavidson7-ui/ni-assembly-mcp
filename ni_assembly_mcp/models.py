"""Type-coercion layer for NI Assembly records.

Every scalar in an NI ``_JSON`` response is a string -- ``"PersonId": "5797"``,
``"QOralAnswerRequested": "false"``, ``"TabledDate": "2026-06-30T00:00:00+01:00"``
(PLAN.md 0 / 2b). Pydantic v2's lax coercion already turns ``"5797"`` into ``int``
and ``"false"`` into ``bool``; this module adds:

* blank strings -> ``None`` (NI uses ``""`` for "no value");
* a tolerant datetime parser (ISO-8601 with offset, plain dates, and the legacy
  ``/Date(ms)/`` form just in case).

Per-domain models (members, questions, ...) subclass :class:`NIABaseModel` and are
added in their own phases.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, TypeVar

from dateutil import parser as _dateparser
from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def parse_ni_datetime(value: Any) -> Any:
    """Best-effort parse of an NI date/datetime string. Passthrough for non-strings."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return None

    # Legacy Microsoft AJAX format: /Date(1750000000000)/ or /Date(...+0100)/
    if text.startswith("/Date(") and text.endswith(")/"):
        inner = text[6:-2].split("+")[0].split("-")[0] if "+" in text[6:-2] else text[6:-2]
        try:
            return datetime.fromtimestamp(int(inner) / 1000, tz=UTC)
        except ValueError:
            return value

    try:
        return _dateparser.isoparse(text)
    except ValueError:
        try:
            return _dateparser.parse(text)
        except (ValueError, OverflowError):
            return value


NIADateTime = Annotated[datetime, BeforeValidator(parse_ni_datetime)]
NIADate = Annotated[date, BeforeValidator(parse_ni_datetime)]


class NIABaseModel(BaseModel):
    """Base for NI Assembly record models.

    * ``extra="allow"`` -- the API adds fields over time; keep unknowns rather
      than dropping data.
    * ``populate_by_name`` -- fields are declared snake_case with ``alias`` set to
      the API's PascalCase key.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_string_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


NIAModelT = TypeVar("NIAModelT", bound=NIABaseModel)


def coerce_records(model: type[NIAModelT], records: list[dict]) -> list[dict]:
    """Validate raw NI records through ``model`` and dump them as plain dicts.

    Declared fields come back snake_case (the aliases carry the API's PascalCase
    key); unknown keys are preserved verbatim (``extra="allow"``). ``None`` values
    are dropped so the payload handed to a model stays lean.
    """
    out: list[dict] = []
    for record in records:
        obj = model.model_validate(record)
        out.append(obj.model_dump(mode="json", by_alias=False, exclude_none=True))
    return out


# --- Reference & list domains (PLAN.md Phase 2) -----------------------------------


class Organisation(NIABaseModel):
    """A row from any ``organisations.asmx`` list: departments, parties, all-party
    groups, committees and the catch-all organisation list all share this shape."""

    organisation_id: int | None = Field(None, alias="OrganisationId")
    organisation_name: str | None = Field(None, alias="OrganisationName")
    organisation_abbreviation: str | None = Field(None, alias="OrganisationAbbreviation")
    organisation_type: str | None = Field(None, alias="OrganisationType")


class Constituency(NIABaseModel):
    constituency_id: int | None = Field(None, alias="ConstituencyId")
    constituency_name: str | None = Field(None, alias="ConstituencyName")
    constituency_ons_code: str | None = Field(None, alias="ConstituencyOnsCode")


class Member(NIABaseModel):
    """A member row as returned by the ``members.asmx`` list/search operations
    (``GetAllCurrentMembers``, ``…BySurnameSearch``, ``…ByGivenDate`` etc.)."""

    person_id: int | None = Field(None, alias="PersonId")
    affiliation_id: int | None = Field(None, alias="AffiliationId")
    member_name: str | None = Field(None, alias="MemberName")
    member_first_name: str | None = Field(None, alias="MemberFirstName")
    member_last_name: str | None = Field(None, alias="MemberLastName")
    member_full_display_name: str | None = Field(None, alias="MemberFullDisplayName")
    member_title: str | None = Field(None, alias="MemberTitle")
    party_name: str | None = Field(None, alias="PartyName")
    party_organisation_id: int | None = Field(None, alias="PartyOrganisationId")
    constituency_name: str | None = Field(None, alias="ConstituencyName")
    constituency_id: int | None = Field(None, alias="ConstituencyId")
    member_image_url: str | None = Field(None, alias="MemberImgUrl")


# --- Member detail, roles & register (PLAN.md Phase 3 / §1a, §1e) -----------------


class MemberRole(NIABaseModel):
    """A single affiliation/role row from ``GetMemberRolesByPersonId`` (full
    history, carries ``AffiliationEnd``) or ``GetAllMemberRoles`` (current roles
    only — no end date). ``RoleType`` is the coarse bucket ("Ministerial Role",
    "Committee Role (incl Assembly Commission)", "All Party Group Role", …)."""

    person_id: int | None = Field(None, alias="PersonId")
    affiliation_id: int | None = Field(None, alias="AffiliationId")
    member_full_display_name: str | None = Field(None, alias="MemberFullDisplayName")
    role_type: str | None = Field(None, alias="RoleType")
    role: str | None = Field(None, alias="Role")
    organisation_id: int | None = Field(None, alias="OrganisationId")
    organisation: str | None = Field(None, alias="Organisation")
    affiliation_title: str | None = Field(None, alias="AffiliationTitle")
    affiliation_start: NIADateTime | None = Field(None, alias="AffiliationStart")
    affiliation_end: NIADateTime | None = Field(None, alias="AffiliationEnd")


class MemberContact(NIABaseModel):
    """One address row from ``GetMemberContactDetailsByPersonId`` — a member has
    a constituency address and an office address, each with its own contact
    details. Note the API's ``EmaiAddress`` key typo, aliased here to ``email``."""

    address_id: int | None = Field(None, alias="AddressId")
    person_id: int | None = Field(None, alias="PersonId")
    address_type: str | None = Field(None, alias="AddressType")
    room_number: str | None = Field(None, alias="RoomNumber")
    address1: str | None = Field(None, alias="Address1")
    townland: str | None = Field(None, alias="Townland")
    ward: str | None = Field(None, alias="Ward")
    town_city: str | None = Field(None, alias="TownCity")
    postcode: str | None = Field(None, alias="Postcode")
    telephone_number: str | None = Field(None, alias="TelephoneNumber")
    email: str | None = Field(None, alias="EmaiAddress")
    latitude: float | None = Field(None, alias="Latitude")
    longitude: float | None = Field(None, alias="Longitude")


class RegisteredInterest(NIABaseModel):
    """A row from ``register.asmx/GetAllRegisteredInterests`` — a declared
    financial interest (employment, donations, gifts, property, …). ``RegisterEntry``
    is free text; ``RegisterCategory`` groups them."""

    person_id: int | None = Field(None, alias="PersonId")
    member_name: str | None = Field(None, alias="MemberName")
    register_category_id: int | None = Field(None, alias="RegisterCategoryId")
    register_category: str | None = Field(None, alias="RegisterCategory")
    register_entry: str | None = Field(None, alias="RegisterEntry")
    register_entry_start_date: NIADateTime | None = Field(None, alias="RegisterEntryStartDate")


# --- Questions (PLAN.md Phase 4 / §6.0-§6.1) -------------------------------------


class Question(NIABaseModel):
    """A parliamentary-question row.

    The ``questions.asmx`` operations return **different field sets** for the same
    record: ``GetQuestionsBySearchText`` is the leanest (no tabler/department/
    answer), the range endpoints are fuller, and ``GetQuestionDetails`` is the
    only one carrying the answer text and ``AnsweredOnDate`` (PLAN.md §6.0). This
    model is the union; absent fields simply come back as ``None`` and are
    dropped by :func:`coerce_records`.

    Department and the oral-answer flag are spelled differently across endpoints
    (``DepartmentId``/``DepartmentID``, ``DepartmentName``/``Department``), hence
    the :class:`~pydantic.AliasChoices`.
    """

    document_id: int | None = Field(None, alias="DocumentId")
    document_type: str | None = Field(None, alias="DocumentType")
    reference: str | None = Field(None, alias="Reference")
    tabled_date: NIADateTime | None = Field(None, alias="TabledDate")
    answer_by_date: NIADateTime | None = Field(None, alias="AnswerByDate")
    answered_on_date: NIADateTime | None = Field(None, alias="AnsweredOnDate")

    question_text: str | None = Field(None, alias="QuestionText")
    question_details_url: str | None = Field(None, alias="QuestionDetails")
    oral_answer_requested: bool | None = Field(
        None, validation_alias=AliasChoices("QOralAnswerRequested", "OralAnswerRequested", "oral_answer_requested")
    )
    priority_request: bool | None = Field(None, alias="PriorityRequest")

    tabler_person_id: int | None = Field(None, alias="TablerPersonId")
    tabler_name: str | None = Field(None, alias="TablerName")
    tabler_title: str | None = Field(None, alias="TablerTitle")
    tabler_affiliation_id: int | None = Field(None, alias="TablerAffiliationId")

    minister_person_id: int | None = Field(None, alias="MinisterPersonId")
    minister_title: str | None = Field(None, alias="MinisterTitle")

    department_id: int | None = Field(
        None, validation_alias=AliasChoices("DepartmentId", "DepartmentID", "department_id")
    )
    department_name: str | None = Field(
        None, validation_alias=AliasChoices("DepartmentName", "Department", "department_name")
    )

    answer_plain_text: str | None = Field(None, alias="AnswerPlainText")
    answer_html: str | None = Field(None, alias="AnswerHtml")
    answer_open_xml: str | None = Field(None, alias="AnswerOpenXml")


# --- Plenary & divisions (PLAN.md Phase 5 / §1e) --------------------------------


class PlenaryItem(NIABaseModel):
    """A tabled plenary item — motion, amendment, ministerial statement, urgent
    oral question, no-day-named motion, … — from ``GetPlenaryItemsTabledDate`` /
    ``…PlenaryDate`` / ``…TabledByMember`` (list shape), ``GetPlenaryDetails``
    (rich single record) and ``GetNoDayNamedMotions``.

    Note the key is ``DocumentID`` (capital ``ID``), *unlike* the questions
    service's ``DocumentId``. ``Text`` is the motion/amendment wording; ``Title``
    is the short heading. ``TabledDate`` is when it was lodged; ``PlenaryDate`` /
    ``ConsideredDate`` when it is (was) scheduled / debated.
    """

    document_id: int | None = Field(None, alias="DocumentID")
    session_id: int | None = Field(None, alias="SessionID")
    session: str | None = Field(None, alias="Session")
    person_id: int | None = Field(None, alias="PersonID")
    title: str | None = Field(None, alias="Title")
    text: str | None = Field(None, alias="Text")
    tabled_date: NIADateTime | None = Field(None, alias="TabledDate")
    considered_date: NIADateTime | None = Field(None, alias="ConsideredDate")
    plenary_date: NIADateTime | None = Field(None, alias="PlenaryDate")
    plenary_type_id: int | None = Field(None, alias="PlenaryTypeID")
    plenary_type: str | None = Field(None, alias="PlenaryType")
    document_type_id: int | None = Field(None, alias="DocumentTypeID")
    document_type: str | None = Field(None, alias="DocumentType")
    motion_category_id: int | None = Field(None, alias="MotionCategoryID")
    motion_category: str | None = Field(None, alias="MotionCategory")
    motion_last_modified: NIADateTime | None = Field(None, alias="MotionLastModified")


class PlenaryTabler(NIABaseModel):
    """A member who tabled (proposed / co-signed) a plenary item, from
    ``GetPlenaryTablers``. ``TablerSequence`` is the sign-up order."""

    document_id: int | None = Field(None, alias="DocumentID")
    tabler_sequence: int | None = Field(None, alias="TablerSequence")
    tabler_person_id: int | None = Field(None, alias="TablerPersonID")
    tabler_name: str | None = Field(None, alias="TablerName")
    tabler_title: str | None = Field(None, alias="TablerTitle")


class PlenaryAddressee(NIABaseModel):
    """The Minister (or body) a plenary item is addressed to, from
    ``GetPlenaryAddressees`` — e.g. the Minister an urgent oral question is put to."""

    document_id: int | None = Field(None, alias="DocumentID")
    addressee_sequence: int | None = Field(None, alias="AddresseeSequence")
    addressee_person_id: int | None = Field(None, alias="AddresseePersonID")
    addressee_name: str | None = Field(None, alias="AddresseeName")
    addressee_title: str | None = Field(None, alias="AddresseeTitle")


class BusinessDiaryItem(NIABaseModel):
    """One scheduled event from ``GetBusinessDiary`` — a plenary sitting, a
    committee meeting, an event in Parliament Buildings. ``EventType`` /
    ``OrganisationName`` say what and whose."""

    event_id: int | None = Field(None, alias="EventId")
    event_date: NIADateTime | None = Field(None, alias="EventDate")
    event_type: str | None = Field(None, alias="EventType")
    event_type_id: int | None = Field(None, alias="EventTypeId")
    organisation_id: int | None = Field(None, alias="OrganisationId")
    organisation_name: str | None = Field(None, alias="OrganisationName")
    start_time: NIADateTime | None = Field(None, alias="StartTime")
    end_time: NIADateTime | None = Field(None, alias="EndTime")
    weekday: str | None = Field(None, alias="Weekday")
    location_room: str | None = Field(None, alias="LocationRoom")
    address: str | None = Field(None, alias="Address")
    address1: str | None = Field(None, alias="Address1")
    town_city: str | None = Field(None, alias="TownCity")


class Division(NIABaseModel):
    """A recorded vote (division) stub from ``GetVotesOnDivision``. ``DocumentID``
    feeds ``get_divisions(document_id=…)`` / ``get_motion_context``. The API
    misspells the type key as ``DivisonType`` — aliased here to ``division_type``."""

    event_id: int | None = Field(None, alias="EventID")
    session_id: int | None = Field(None, alias="SessionID")
    document_id: int | None = Field(None, alias="DocumentID")
    division_subject: str | None = Field(None, alias="DivisionSubject")
    division_date: NIADateTime | None = Field(None, alias="DivisionDate")
    division_type: str | None = Field(
        None, validation_alias=AliasChoices("DivisonType", "DivisionType", "division_type")
    )


class DivisionResult(NIABaseModel):
    """The outcome of a division from ``GetDivisionResult`` — the aye/no/abstention
    tallies, broken down by community designation, plus the ``Outcome`` string."""

    event_id: int | None = Field(None, alias="EventId")
    event_date: NIADateTime | None = Field(None, alias="EventDate")
    title: str | None = Field(None, alias="Title")
    document_id: int | None = Field(None, alias="DocumentID")
    decision_method: str | None = Field(None, alias="DecisionMethod")
    outcome: str | None = Field(None, alias="Outcome")
    decision_type: str | None = Field(None, alias="DecisionType")
    total_ayes: int | None = Field(None, alias="TotalAyes")
    nationalist_ayes: int | None = Field(None, alias="NationalistAyes")
    unionist_ayes: int | None = Field(None, alias="UnionistAyes")
    other_ayes: int | None = Field(None, alias="OtherAyes")
    total_noes: int | None = Field(None, alias="TotalNoes")
    nationalist_noes: int | None = Field(None, alias="NationalistNoes")
    unionist_noes: int | None = Field(None, alias="UnionistNoes")
    other_noes: int | None = Field(None, alias="OtherNoes")
    total_abstentions: int | None = Field(None, alias="TotalAbstentions")
    nationalist_abstentions: int | None = Field(None, alias="NationalistAbstentions")
    unionist_abstentions: int | None = Field(None, alias="UnionistAbstentions")
    other_abstentions: int | None = Field(None, alias="OtherAbstentions")


class DivisionMemberVote(NIABaseModel):
    """How one member voted in a division, from ``GetDivisionMemberVoting``.
    ``Vote`` is ``AYE`` / ``NO`` / ``ABSTAINED``; ``Designation`` is the member's
    community designation at the time."""

    document_id: int | None = Field(None, alias="DocumentID")
    event_id: int | None = Field(None, alias="EventID")
    person_id: int | None = Field(None, alias="PersonID")
    member_name: str | None = Field(None, alias="MemberName")
    vote: str | None = Field(None, alias="Vote")
    designation: str | None = Field(None, alias="Designation")
    vote_in_vacancy: bool | None = Field(None, alias="VoteInVacancy")
    member_sort_name: str | None = Field(None, alias="MemberSortName")


class MotionAmendment(NIABaseModel):
    """An amendment tabled to a motion, from ``GetMotionAmendments``.
    ``ParentDocumentID`` is the motion it amends; ``Text`` is the amendment wording."""

    document_id: int | None = Field(None, alias="DocumentID")
    parent_document_id: int | None = Field(None, alias="ParentDocumentID")
    tabled_date: NIADateTime | None = Field(None, alias="TabledDate")
    text: str | None = Field(None, alias="Text")


class MotionBill(NIABaseModel):
    """The Bill a motion relates to, from ``GetMotionBill`` (e.g. a Bill stage
    motion). ``ParentDocumentID`` is the motion."""

    document_id: int | None = Field(None, alias="DocumentID")
    parent_document_id: int | None = Field(None, alias="ParentDocumentID")
    reference_number: str | None = Field(None, alias="ReferenceNumber")
    bill_type: str | None = Field(None, alias="BillType")
    bill_name: str | None = Field(None, alias="BillName")
    short_title: str | None = Field(None, alias="ShortTitle")
    long_title: str | None = Field(None, alias="LongTitle")
    stage: str | None = Field(None, alias="Stage")
    is_accelerated_passage: bool | None = Field(None, alias="IsAcceleratedPassage")


class HansardReport(NIABaseModel):
    """One sitting-day stub from ``hansard.asmx/GetAllHansardReports`` — the index
    of Official Report volumes. Carries no titles (those live in the components /
    the FTS5 index); ``PlenaryDate`` is the sitting date."""

    report_doc_id: int | None = Field(None, alias="ReportDocId")
    plenary_date: NIADateTime | None = Field(None, alias="PlenaryDate")
    plenary_session_id: int | None = Field(None, alias="PlenarySessionId")
    plenary_session_name: str | None = Field(None, alias="PlenarySessionName")


class MotionPetitionOfConcern(NIABaseModel):
    """A Petition of Concern lodged against a motion, from
    ``GetMotionPetitionOfConcern`` — an NI-specific cross-community veto mechanism.

    Shape verified against one historical example (doc 247608, the PoC against the
    Nov-2015 Marriage Equality motion): ``DocumentID`` (the PoC's own id),
    ``ParentDocumentID`` (the motion), ``TabledDate``, ``PlenaryDate``, ``Title``.
    The endpoint does **not** return the signatories. No PoC has been lodged in
    the current mandate; ``extra="allow"`` keeps any additional keys a future one
    might carry.
    """

    document_id: int | None = Field(None, alias="DocumentID")
    parent_document_id: int | None = Field(None, alias="ParentDocumentID")
    title: str | None = Field(None, alias="Title")
    tabled_date: NIADateTime | None = Field(None, alias="TabledDate")
    plenary_date: NIADateTime | None = Field(None, alias="PlenaryDate")


# --- Committee agendas (PLAN.md Phase 7 / §3 — XML-only) ------------------------


class CommitteeAgendaItem(NIABaseModel):
    """One agenda item for a committee meeting, from the XML-only
    ``plenary.asmx/GetCommitteeAgendaItems*`` operations (PLAN.md §3). This API has
    no JSON committee-agenda endpoint, so :func:`~ni_assembly_mcp.niassembly_client.niassembly_get_xml`
    parses the ``<ItemList><Committee>…</Committee></ItemList>`` body.

    A meeting has several items (``ItemOrder`` is the running order); a sitting day
    has several meetings. ``EventId`` matches the ``get_business_diary`` event id,
    so that tool finds the meeting and this one lists its business. ``Session`` is
    the public/closed status plus the time span; ``SessionStart`` / ``SessionEnd``
    are wall-clock times of day (``"09:15"``), not dates.
    """

    event_id: int | None = Field(None, alias="EventId")
    item_id: int | None = Field(None, alias="ItemId")
    item_of_business: str | None = Field(None, alias="ItemOfBusiness")
    item_type: str | None = Field(None, alias="ItemType")
    item_order: int | None = Field(None, alias="ItemOrder")
    committee_name: str | None = Field(None, alias="CommitteeName")
    organisation_id: int | None = Field(None, alias="OrganisationId")
    meeting_date: NIADateTime | None = Field(None, alias="MeetingDate")
    session_start: str | None = Field(None, alias="SessionStart")
    session_end: str | None = Field(None, alias="SessionEnd")
    session: str | None = Field(None, alias="Session")
    session_from: str | None = Field(None, alias="SessionFrom")
    session_last_modified: NIADateTime | None = Field(None, alias="SessionLastModified")

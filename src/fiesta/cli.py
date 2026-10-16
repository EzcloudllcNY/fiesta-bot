"""The fiesta command line.

``tick`` and ``send-due`` exist because the outbox makes them safe to run by
hand: both deduplicate, so they double as the recovery tools when something has
gone wrong in production.
"""

import logging
from datetime import date
from typing import TYPE_CHECKING, Annotated, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from fiesta.config import load_config
from fiesta.db import init_engine, session_scope
from fiesta.monitoring import init_sentry

if TYPE_CHECKING:
    from sqlmodel import Session

    from fiesta.models import Birthday, Member
    from fiesta.services import celebration, upcoming

app = typer.Typer(
    help="Slack bot that tracks team birthdays.", no_args_is_help=True
)
settings_app = typer.Typer(
    help="Read and change admin settings.", no_args_is_help=True
)
templates_app = typer.Typer(
    help="Manage congratulation texts.", no_args_is_help=True
)
users_app = typer.Typer(
    help="See the roster and who hears about upcoming birthdays.",
    no_args_is_help=True,
)
gifs_app = typer.Typer(
    help="Manage the celebration GIF pool.", no_args_is_help=True
)
app.add_typer(settings_app, name="settings")
app.add_typer(users_app, name="users")
app.add_typer(templates_app, name="templates")
app.add_typer(gifs_app, name="gifs")

console = Console()


def _setup(*, require_slack: bool):
    config = load_config(require_slack=require_slack)
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    init_sentry(config)
    init_engine(config.database_url)
    return config


@app.command()
def run() -> None:
    """Start the bot: Socket Mode listener plus the background scheduler."""
    from fiesta.scheduler import build_scheduler, run_startup_tasks
    from fiesta.slack.app import build_app, build_handler

    config = _setup(require_slack=True)
    bolt_app = build_app(config)
    client = bolt_app.client

    run_startup_tasks(client)

    scheduler = build_scheduler(client)
    scheduler.start()
    console.print("[green]Fiesta is running.[/green] Press Ctrl+C to stop.")

    try:
        build_handler(bolt_app, config).start()
    except KeyboardInterrupt:
        console.print("\nStopping…")
    finally:
        scheduler.shutdown(wait=False)


@app.command(name="send-due")
def send_due() -> None:
    """Deliver every message that is currently due, then exit."""
    from fiesta.slack.app import build_app
    from fiesta.slack.sender import drain

    config = _setup(require_slack=True)
    client = build_app(config).client

    with session_scope() as session:
        sent = drain(session, client)

    console.print(f"Sent [bold]{sent}[/bold] message(s).")


@app.command()
def tick(
    day: str | None = typer.Option(
        None,
        "--date",
        help="Run the tick for this date (YYYY-MM-DD) instead of today.",
    ),
) -> None:
    """Queue the messages that belong to a given day. Safe to repeat."""
    from fiesta import clock
    from fiesta.repo import settings as settings_repo
    from fiesta.services import daily
    from fiesta.services.celebration import Outcome as CelebrationOutcome

    _setup(require_slack=False)

    with session_scope() as session:
        target = (
            date.fromisoformat(day)
            if day
            else clock.today_in(settings_repo.get(session, "timezone"))
        )
        result = daily.run(session, target)
        channel = settings_repo.get(session, "celebration_channel")
        auto_send = settings_repo.get(session, "auto_send_enabled")

    console.print(f"[bold]{result.day}[/bold]")
    console.print(
        f"  upcoming DMs  {result.upcoming.outcome.value} — "
        f"{_upcoming_reason(result.upcoming)}"
    )
    console.print(
        f"  celebration   {result.celebration.value} — "
        f"{_celebration_reason(result.celebration, result.day, channel)}"
    )

    if (result.upcoming.queued or result.celebration_queued) and not auto_send:
        console.print(
            "[yellow]auto_send_enabled is false.[/yellow] "
            "Run `fiesta send-due` to deliver."
        )
    if result.celebration is CelebrationOutcome.NOT_CONFIGURED:
        console.print(
            "[yellow]No celebration channel set.[/yellow] "
            "The day stays replayable — set one with "
            "`fiesta settings set celebration_channel C...` "
            "and run this again."
        )


def _upcoming_reason(result: upcoming.Result) -> str:
    from fiesta.services.upcoming import Outcome

    on = f"birthdays on {result.target}"
    match result.outcome:
        case Outcome.QUEUED:
            return f"{result.queued} DM(s) about {on}"
        case Outcome.ALREADY_QUEUED:
            return f"DMs about {on} were queued by an earlier run"
        case Outcome.NOTHING_TO_SEND:
            return f"no {on}"
        case Outcome.NO_RECIPIENTS:
            return f"{on}, but everyone else muted heads-ups"
        case Outcome.PASSED:
            return f"{on} already passed"
        case Outcome.DISABLED:
            return "upcoming_enabled is false"


def _celebration_reason(
    outcome: celebration.Outcome, day: date, channel: str | None
) -> str:
    from fiesta.services.celebration import Outcome

    match outcome:
        case Outcome.QUEUED:
            return f"posts for {day} in {channel}"
        case Outcome.ALREADY_QUEUED:
            return f"posts for {day} were queued by an earlier run"
        case Outcome.NOTHING_TO_POST:
            return f"no birthdays on {day}"
        case Outcome.NOT_CONFIGURED:
            return "celebration_channel is not set"
        case Outcome.DISABLED:
            return "celebration_enabled is false"


@app.command()
def sync() -> None:
    """Pull the workspace roster from Slack now, without messaging anyone."""
    from fiesta.services import roster
    from fiesta.slack.app import build_app

    config = _setup(require_slack=True)
    client = build_app(config).client

    with session_scope() as session:
        result = roster.sync(session, client)

    console.print(
        f"{result.seen} user(s) seen, {result.deactivated} deactivated, "
        f"{result.newly_queued} newly queued for a birthday ask."
    )


@app.command()
def announce(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List who would be messaged, then stop."
    ),
) -> None:
    """Introduce the bot to the workspace and ask everyone for their birthday.

    Run `fiesta sync` first so the roster is current. This is the rollout
    message, sent once: it takes the place of each person's first ask, so nobody
    gets both this and the welcome meant for new joiners.
    """
    from fiesta.services import onboarding

    _setup(require_slack=False)

    with session_scope() as session:
        recipients = onboarding.introduction_recipients(session)
        if dry_run:
            table = Table("Would be messaged", "Name")
            for member in recipients:
                table.add_row(member.slack_user_id, member.display_name)
            console.print(table)
            console.print(
                f"[yellow]Dry run[/yellow] — {len(recipients)} DM(s), "
                "nothing queued."
            )
            return

        queued = onboarding.enqueue_introductions(session)

    console.print(
        f"Queued [bold]{queued}[/bold] introduction DM(s). "
        "The running bot will deliver them; otherwise run `fiesta send-due`."
    )


@app.command()
def migrate() -> None:
    """Bring the database up to the latest schema."""
    from fiesta.migrations import upgrade_to_head

    config = _setup(require_slack=False)
    upgrade_to_head(config.database_url)
    console.print("[green]Database is up to date.[/green]")


@settings_app.command("list")
def settings_list() -> None:
    """Show every setting, its current value, and what it does."""
    from fiesta.repo import settings as settings_repo

    _setup(require_slack=False)

    with session_scope() as session:
        stored = settings_repo.stored_values(session)

    table = Table("Setting", "Value", "Default", "Meaning")
    for key, spec in settings_repo.SPECS.items():
        value = stored.get(key, spec.default)
        shown = _show(value) if key in stored else f"[dim]{_show(value)}[/dim]"
        table.add_row(key, shown, _show(spec.default), spec.description)

    console.print(table)
    console.print("[dim]Dimmed values are defaults.[/dim]")


@settings_app.command("get")
def settings_get(key: str) -> None:
    """Print one setting's current value."""
    from fiesta.repo import settings as settings_repo

    _setup(require_slack=False)

    _known_setting(key)
    with session_scope() as session:
        value = settings_repo.get(session, key)

    # Plain print, no markup, so the value can be read by a script.
    print(_show(value))


@settings_app.command("set")
def settings_set(key: str, value: str) -> None:
    """Change a setting.

    Switches take true/false; `reset` goes back to the default.
    """
    from fiesta.repo import settings as settings_repo

    _setup(require_slack=False)

    _known_setting(key)
    try:
        parsed = settings_repo.parse(key, value)
    except settings_repo.InvalidValue as exc:
        console.print(f"[red]Invalid value for {key}:[/red] {exc}")
        raise typer.Exit(1) from None

    with session_scope() as session:
        settings_repo.set_value(session, key, parsed)

    console.print(f"{key} = {_show(parsed)}")
    _restart_hint(key)


@settings_app.command("reset")
def settings_reset(key: str) -> None:
    """Put a setting back to its default."""
    from fiesta.repo import settings as settings_repo

    _setup(require_slack=False)

    spec = _known_setting(key)
    with session_scope() as session:
        changed = settings_repo.reset(session, key)

    default = _show(spec.default)
    if not changed:
        console.print(f"{key} is already the default ({default}).")
        return

    console.print(f"{key} = {default} (default)")
    _restart_hint(key)


def _known_setting(key: str):
    """The setting's spec, or a helpful exit for a misspelled name."""
    from fiesta.repo import settings as settings_repo

    try:
        return settings_repo.spec_for(key)
    except settings_repo.UnknownSetting:
        known = ", ".join(settings_repo.SPECS)
        console.print(
            f"[red]Unknown setting {key!r}.[/red] Known settings: {known}"
        )
        raise typer.Exit(1) from None


def _show(value: object) -> str:
    """A value as an admin would type it.

    true/false for switches, bare strings, - for unset.
    """
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _restart_hint(key: str) -> None:
    from fiesta.repo import settings as settings_repo

    if settings_repo.SPECS[key].needs_restart:
        console.print(
            "[yellow]The running bot reads this at startup — "
            "restart it to apply.[/yellow]"
        )


@users_app.command("list")
def users_list(
    show_all: bool = typer.Option(
        False, "--all", help="Include bots and deactivated accounts."
    ),
) -> None:
    """Show members with their birthday and mute state."""
    from fiesta.repo import birthdays as birthdays_repo
    from fiesta.repo import members as members_repo

    _setup(require_slack=False)

    columns = ["ID", "Name", "Birthday", "Muted"]
    table = Table(*columns, "Status") if show_all else Table(*columns)

    with session_scope() as session:
        birthdays = birthdays_repo.by_member(session)
        muted = members_repo.muted_ids(session)
        for member in members_repo.everyone(session):
            if not (show_all or member.is_active):
                continue
            row = [
                member.slack_user_id,
                member.display_name,
                _birthday(birthdays.get(member.slack_user_id)),
                _show(member.slack_user_id in muted),
            ]
            if show_all:
                row.append(_status(member))
            table.add_row(*row)

    console.print(table)
    console.print(f"[dim]{table.row_count} member(s).[/dim]")


@users_app.command("show")
def users_show(user_id: str) -> None:
    """Show everything stored about one member."""
    from fiesta.repo import asks as asks_repo
    from fiesta.repo import birthdays as birthdays_repo
    from fiesta.repo import members as members_repo

    _setup(require_slack=False)

    with session_scope() as session:
        member = _known_member(session, user_id)
        birthday = birthdays_repo.get(session, user_id)
        muted = user_id in members_repo.muted_ids(session)
        ask = asks_repo.get(session, user_id)

        table = Table(show_header=False, box=None)
        table.add_row("ID", member.slack_user_id)
        table.add_row("Name", member.display_name)
        table.add_row("Real name", member.real_name or "-")
        table.add_row("Timezone", member.tz or "-")
        table.add_row("Status", _status(member))
        table.add_row("Birthday", _birthday(birthday))
        table.add_row("Muted", _show(muted))
        table.add_row(
            "Ask",
            f"{ask.status.value}, {ask.asks_sent} sent" if ask else "-",
        )

    console.print(table)


@users_app.command("mute")
def users_mute(
    user_ids: Annotated[list[str] | None, typer.Argument()] = None,
    all_except: Annotated[
        list[str] | None,
        typer.Option(
            "--all-except",
            help="Mute every active member but these. Repeatable.",
        ),
    ] = None,
) -> None:
    """Stop upcoming-birthday DMs going to members."""
    from fiesta.repo import members as members_repo

    _setup(require_slack=False)

    if bool(user_ids) == bool(all_except):
        console.print("[red]Give either user IDs or --all-except.[/red]")
        raise typer.Exit(1)

    with session_scope() as session:
        if all_except:
            for user_id in all_except:
                _known_member(session, user_id)
            targets = [
                m.slack_user_id
                for m in members_repo.active(session)
                if m.slack_user_id not in all_except
            ]
        else:
            targets = [
                _known_member(session, u).slack_user_id for u in user_ids or []
            ]

        for user_id in targets:
            members_repo.set_muted_upcoming(session, user_id, True)

    console.print(f"Muted {len(targets)} member(s).")
    console.print("[dim]Undo with: fiesta users unmute --all[/dim]")


@users_app.command("unmute")
def users_unmute(
    user_ids: Annotated[list[str] | None, typer.Argument()] = None,
    everyone: bool = typer.Option(
        False, "--all", help="Unmute every muted member."
    ),
) -> None:
    """Let upcoming-birthday DMs reach members again."""
    from fiesta.repo import members as members_repo

    _setup(require_slack=False)

    if bool(user_ids) == everyone:
        console.print("[red]Give either user IDs or --all.[/red]")
        raise typer.Exit(1)

    with session_scope() as session:
        if everyone:
            targets = sorted(members_repo.muted_ids(session))
        else:
            targets = [
                _known_member(session, u).slack_user_id for u in user_ids or []
            ]

        for user_id in targets:
            members_repo.set_muted_upcoming(session, user_id, False)

    console.print(f"Unmuted {len(targets)} member(s).")


def _known_member(session: Session, user_id: str) -> Member:
    """The member, or a helpful exit for an ID the roster doesn't have."""
    from fiesta.repo import members as members_repo

    member = members_repo.get(session, user_id)
    if member is None:
        console.print(
            f"[red]Unknown user {user_id!r}.[/red] "
            "See `fiesta users list --all`."
        )
        raise typer.Exit(1)
    return member


def _birthday(birthday: Birthday | None) -> str:
    """MM-DD, never the year: nothing shows that back."""
    if birthday is None:
        return "-"
    shown = f"{birthday.month:02d}-{birthday.day:02d}"
    return f"{shown} (hidden)" if birthday.is_hidden else shown


def _status(member: Member) -> str:
    if member.is_deleted:
        return "deactivated"
    return "bot" if member.is_bot else "active"


@templates_app.command("list")
def templates_list() -> None:
    """Show every congratulation text."""
    from fiesta.repo import templates as templates_repo

    _setup(require_slack=False)

    table = Table("ID", "Enabled", "Text")
    with session_scope() as session:
        for template in templates_repo.all_templates(session):
            table.add_row(
                str(template.id), _show(template.enabled), template.text
            )

    console.print(table)


@templates_app.command("add")
def templates_add(text: str) -> None:
    """Add a congratulation text. {user} marks where the mention goes."""
    from fiesta.repo import templates as templates_repo

    _setup(require_slack=False)

    with session_scope() as session:
        try:
            template = templates_repo.add(session, text=text)
        except ValueError as exc:
            _fail(str(exc))
        session.flush()
        console.print(f"[green]Added template {template.id}.[/green]")


@templates_app.command("edit")
def templates_edit(
    template_id: int,
    text: Annotated[
        str | None, typer.Argument(help="New text, with {user}.")
    ] = None,
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Use it, or keep it aside."),
    ] = None,
) -> None:
    """Change a text, or switch it on or off."""
    from fiesta.repo import templates as templates_repo

    _setup(require_slack=False)

    if text is None and enabled is None:
        _fail("Nothing to change. Give a new text, --enabled or --disabled.")

    with session_scope() as session:
        template = templates_repo.get(session, template_id)
        if template is None:
            _fail(f"No template with ID {template_id}.")
        try:
            templates_repo.update(session, template, text=text, enabled=enabled)
        except ValueError as exc:
            _fail(str(exc))

    console.print(f"[green]Template {template_id} updated.[/green]")


@templates_app.command("remove")
def templates_remove(template_id: int) -> None:
    """Delete a text for good. --disabled on edit keeps it for later."""
    from fiesta.repo import templates as templates_repo

    _setup(require_slack=False)

    with session_scope() as session:
        template = templates_repo.get(session, template_id)
        if template is None:
            _fail(f"No template with ID {template_id}.")
        templates_repo.remove(session, template)

    console.print(f"[green]Template {template_id} removed.[/green]")


@gifs_app.command("list")
def gifs_list() -> None:
    """Show every GIF in the pool."""
    from fiesta.models import MAX_FAILURES
    from fiesta.repo import gifs as gifs_repo

    _setup(require_slack=False)

    table = Table("ID", "Enabled", "Fails", "Last used", "Tags", "URL")
    with session_scope() as session:
        for gif in gifs_repo.all_gifs(session):
            fails = str(gif.fail_count)
            if gif.fail_count >= MAX_FAILURES:
                fails = f"[red]{fails} (retired)[/red]"
            last_used = gif.last_used_at
            table.add_row(
                str(gif.id),
                _show(gif.enabled),
                fails,
                f"{last_used:%Y-%m-%d}" if last_used else "-",
                gif.tags or "-",
                gif.url,
            )

    console.print(table)


@gifs_app.command("add")
def gifs_add(url: str, tags: str | None = typer.Option(None)) -> None:
    """Add a GIF URL to the celebration pool."""
    from fiesta.repo import gifs as gifs_repo

    _setup(require_slack=False)

    with session_scope() as session:
        try:
            gif = gifs_repo.add(session, url=url, tags=tags)
        except ValueError as exc:
            _fail(str(exc))
        session.flush()
        console.print(f"[green]Added GIF {gif.id}.[/green]")

    console.print("Enable GIFs with: fiesta settings set gif_provider db")


@gifs_app.command("edit")
def gifs_edit(
    gif_id: int,
    url: str | None = typer.Option(None, help="A new URL; resets the fails."),
    tags: str | None = typer.Option(None, help='New tags; "" clears them.'),
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Use it, or keep it aside."),
    ] = None,
) -> None:
    """Change a GIF's URL or tags, or switch it on or off."""
    from fiesta.repo import gifs as gifs_repo

    _setup(require_slack=False)

    if url is None and tags is None and enabled is None:
        _fail("Nothing to change. Give --url, --tags, --enabled or --disabled.")

    with session_scope() as session:
        gif = gifs_repo.get(session, gif_id)
        if gif is None:
            _fail(f"No GIF with ID {gif_id}.")
        try:
            gifs_repo.update(session, gif, url=url, tags=tags, enabled=enabled)
        except ValueError as exc:
            _fail(str(exc))

    console.print(f"[green]GIF {gif_id} updated.[/green]")


@gifs_app.command("remove")
def gifs_remove(gif_id: int) -> None:
    """Delete a GIF for good. --disabled on edit keeps it for later."""
    from fiesta.repo import gifs as gifs_repo

    _setup(require_slack=False)

    with session_scope() as session:
        gif = gifs_repo.get(session, gif_id)
        if gif is None:
            _fail(f"No GIF with ID {gif_id}.")
        gifs_repo.remove(session, gif)

    console.print(f"[green]GIF {gif_id} removed.[/green]")


def _fail(message: str) -> NoReturn:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)

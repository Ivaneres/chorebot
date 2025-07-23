import discord
from discord import app_commands
from data import Frequency, Chore, Datastore
from datetime import date, datetime, timedelta
import asyncio
from emoji import is_emoji
import os


DATA_FILE = "data.json"
INTRO_MESSAGE = "Welcome to ChoreBot!\n\nTo get started, set your emoji using the \"/set_emoji\" command, then set some chores using the \"/add_chore\" command."

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Initialize the datastore
DATASTORE = Datastore.load_from_file("data.json", client)


@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    try:
        # Sync the command tree with Discord
        await tree.sync()
        print("Slash commands synchronized successfully.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    client.loop.create_task(schedule_reminders())


@tree.command(
    name="setup_channel",
    description="Sets up ChoreBot in this channel"
)
async def setup_channel(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    DATASTORE.chore_channel_id = channel_id
    DATASTORE.save_to_file()

    # Delete all messages in the channel
    channel = interaction.channel
    await channel.purge()

    # Send the intro message after purging
    await interaction.response.send_message(INTRO_MESSAGE)


def parse_frequency(frequency: str) -> Frequency:
    frequency = frequency.lower()
    if any(x in frequency for x in ["day", "daily"]):
        return Frequency.DAY
    if "week" in frequency:
        return Frequency.WEEK
    if "month" in frequency:
        return Frequency.MONTH
    if "year" in frequency:
        return Frequency.YEAR
    raise ValueError(f"{frequency} is not a valid frequency")


def parse_schedule(schedule: str) -> tuple[int, Frequency]:
    """
    Parses the schedule string into repeats and frequency.

    Args:
        schedule (str): The schedule string in the format "repeats/frequency".

    Returns:
        tuple[int, Frequency]: A tuple containing the number of repeats and the frequency.
    """
    if len(schedule.split("/")) != 2:
        raise ValueError("Schedule must follow 'repeats/frequency' format")

    schedule_parts = schedule.split("/")
    repeats = int(schedule_parts[0])
    frequency = parse_frequency(schedule_parts[1])

    if repeats < 1:
        raise ValueError("Repeats must be at least 1")

    return repeats, frequency


def parse_date(date_str: str) -> date:
    """
    Parses a date string into a date object. Supports multiple formats.

    Args:
        date_str (str): The date string to parse.

    Returns:
        date: The parsed date object.

    Raises:
        ValueError: If the date string does not match any supported format.
    """
    formats = [
        "%d/%m/%Y",  # e.g., 20/07/2025
        "%d/%m/%y",  # e.g., 20/7/25
        "%d-%m-%Y",  # e.g., 20-07-2025
        "%d-%m-%y",  # e.g., 20-7-25
        "%d %b %Y",  # e.g., 20 Jul 2025
        "%d %B %Y",  # e.g., 20 July 2025
        "%d %b %y",  # e.g., 20 Jul 25
        "%d %B %y",  # e.g., 20 July 25
        "%dth %b %Y",  # e.g., 20th Jul 2025
        "%dth %B %Y",  # e.g., 20th July 2025
        "%dnd %b %Y",  # e.g., 22nd Jul 2025
        "%dnd %B %Y",  # e.g., 22nd July 2025
        "%drd %b %Y",  # e.g., 23rd Jul 2025
        "%drd %B %Y",  # e.g., 23rd July 2025
        "%dst %b %Y",  # e.g., 21st Jul 2025
        "%dst %B %Y",  # e.g., 21st July 2025
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Date '{date_str}' is not in a recognized format.")


def check_user_has_emoji(user_id: str, user_emojis: dict) -> bool:
    """
    Checks if a user has an emoji set in the user_emojis dictionary.

    Args:
        user_id (str): The ID of the user to check.
        user_emojis (dict): A dictionary mapping user IDs to their emojis.

    Returns:
        bool: True if the user has an emoji set, False otherwise.
    """
    return user_id in user_emojis


@tree.command(
    name="add_chore",
    description="Adds a chore to the list",
)
async def add_chore(interaction: discord.Interaction, title: str, assignee: discord.User, start_date: str = None, schedule: str = None):
    if DATASTORE.chore_channel_id == 0:
        await interaction.response.send_message("Must set up a channel first.")
        return
    if DATASTORE.chore_channel_id != interaction.channel_id:
        await interaction.response.send_message("This is not allowed here, please run this command in your chore channel.")
        return

    # Check for duplicate chore titles
    if any(chore.title == title for chore in DATASTORE.chores):
        await interaction.response.send_message(f"A chore with the title '{title}' already exists.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    # Check if the assignee has an emoji set
    if assignee and str(assignee.id) not in DATASTORE.user_emojis:
        await interaction.response.send_message(f"Error: {assignee.mention} does not have an emoji set. Please set an emoji first using the `/set_emoji` command.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    # Allow chores with no due date, frequency, or repeats
    repeats = None
    frequency = None
    start_date_parsed = None

    if schedule:
        try:
            repeats, frequency = parse_schedule(schedule)
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
            return

    if start_date:
        try:
            start_date_parsed = parse_date(start_date)
            if start_date_parsed < date.today():
                raise ValueError("Start date cannot be in the past.")
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
            return

    # Create a new chore object
    chore = Chore(
        title=title,
        repeats=repeats,
        frequency=frequency,
        assignee=assignee,
        due_date=start_date_parsed,
    )

    # Save the chore to the datastore
    DATASTORE.chores.append(chore)
    DATASTORE.save_to_file()

    # Send a message to the channel with the chore details
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel is None:
        await interaction.response.send_message("Error: Could not find the channel.")
        return

    message_content = f"**Chore:** {chore.title}\n"
    if chore.due_date:
        message_content += f"**Due Date:** {chore.due_date.strftime('%d/%m/%Y')}\n"
    if chore.frequency:
        message_content += f"**Frequency:** {chore.frequency.value}\n"
    if chore.repeats:
        message_content += f"**Repeats:** {chore.repeats}\n"
    message_content += f"**Assigned To:** {assignee.mention if assignee else 'Unassigned'}"

    message = await channel.send(message_content)

    # React with the assigned user's emoji if available
    if assignee:
        await message.add_reaction(DATASTORE.user_emojis[str(assignee.id)])

    await interaction.response.send_message(f"Chore '{title}' added successfully!")
    await asyncio.sleep(10)
    await interaction.delete_original_response()


@tree.command(
    name="set_emoji",
    description="Sets your emoji"
)
async def set_emoji(interaction: discord.Interaction, emoji: str):
    """
    Links the given emoji to the user in the DB, so it can be used to raise chore reactions.
    """
    # verify that the emoji is valid (no text, only one emoji)
    if not emoji or not is_emoji(emoji):
        await interaction.response.send_message("Please provide a valid single emoji.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    user_id = str(interaction.user.id)
    old_emoji = DATASTORE.user_emojis.get(user_id)
    DATASTORE.user_emojis[user_id] = emoji
    DATASTORE.save_to_file()

    # Loop through all existing messages and change the reactions to the new emoji for this user
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel:
        messages = [message async for message in channel.history(limit=100)]
        for message in messages:
            if any(reaction.emoji == old_emoji for reaction in message.reactions):
                # Remove the old reaction and add the new one
                await message.clear_reactions()
                await message.add_reaction(emoji)

    await interaction.response.send_message(f"Emoji set to {emoji} for you.")
    await asyncio.sleep(10)
    await interaction.delete_original_response()


@tree.command(
    name="edit_chore",
    description="Edits an existing chore",
)
async def edit_chore(interaction: discord.Interaction, title: str, new_title: str = None, new_due_date: str = None, new_assignee: discord.User = None, new_frequency: str = None, new_repeats: str = None):
    # Find the chore to edit
    chore = next((c for c in DATASTORE.chores if c.title == title), None)
    if chore is None:
        await interaction.response.send_message(f"Chore '{title}' not found.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    # Check if the new assignee has an emoji set
    if new_assignee and str(new_assignee.id) not in DATASTORE.user_emojis:
        await interaction.response.send_message(f"Error: {new_assignee.mention} does not have an emoji set. Please set an emoji first using the `/set_emoji` command.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    # Update the chore details
    if new_title:
        chore.title = new_title

    if new_due_date == "None":
        chore.due_date = None
        chore.frequency = None
        chore.repeats = None
    elif new_due_date:
        try:
            chore.due_date = parse_date(new_due_date)  # Validate the date format
            if chore.due_date < date.today():
                raise ValueError("Due date cannot be in the past.")
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
            return

    if new_assignee:
        chore.assignee = new_assignee

    if new_frequency:
        try:
            chore.frequency = parse_frequency(new_frequency)
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
            return

    if new_repeats == "None":
        chore.repeats = None
    elif new_repeats:
        try:
            chore.repeats = int(new_repeats)
        except ValueError:
            await interaction.response.send_message("Error: Repeats must be an integer.")
            await asyncio.sleep(10)
            await interaction.delete_original_response()
            return

    # Save the updated chores back to the datastore
    DATASTORE.save_to_file()

    # Regenerate the original message in the channel
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel:
        messages = [message async for message in channel.history(limit=100)]
        for message in messages:
            if f"**Chore:** {title}" in message.content:
                message_content = f"**Chore:** {chore.title}\n"
                if chore.due_date:
                    message_content += f"**Due Date:** {chore.due_date.strftime('%d/%m/%Y')}\n"
                if chore.frequency:
                    message_content += f"**Frequency:** {chore.frequency.value}\n"
                if chore.repeats:
                    message_content += f"**Repeats:** {chore.repeats}\n"
                message_content += f"**Assigned To:** {chore.assignee.mention if chore.assignee else 'Unassigned'}"
                await message.edit(content=message_content)
                if new_assignee:
                    await message.clear_reactions()
                    await message.add_reaction(DATASTORE.user_emojis[str(chore.assignee.id)])
                break

    await interaction.response.send_message(f"Chore '{title}' updated successfully!")
    await asyncio.sleep(10)
    await interaction.delete_original_response()


@tree.command(
    name="delete_chore",
    description="Deletes an existing chore",
)
async def delete_chore(interaction: discord.Interaction, title: str):
    # Find the chore to delete
    chore = next((c for c in DATASTORE.chores if c.title == title), None)
    if chore is None:
        await interaction.response.send_message(f"Chore '{title}' not found.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    # Remove the chore from the list
    DATASTORE.chores.remove(chore)

    # Save the updated chores back to the datastore
    DATASTORE.save_to_file()

    # Delete the original message in the chore channel
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel:
        messages = [message async for message in channel.history(limit=100)]
        for message in messages:
            if f"**Chore:** {title}" in message.content:
                await message.delete()
                break

    await interaction.response.send_message(f"Chore '{title}' deleted successfully!")
    await asyncio.sleep(10)
    await interaction.delete_original_response()


@tree.command(
    name="set_reminder_channel",
    description="Sets up a separate channel for reminders"
)
async def set_reminder_channel(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    DATASTORE.reminder_channel_id = channel_id
    DATASTORE.save_to_file()
    response = await interaction.response.send_message("This channel has been set as the reminder channel.")


async def send_reminder(chore: Chore, reminder_type: str):
    """
    Sends a reminder for a chore.

    Args:
        chore (Chore): The chore object.
        reminder_type (str): The type of reminder (e.g., "Upcoming", "Due Today").
    """
    reminder_channel_id = DATASTORE.reminder_channel_id
    if reminder_channel_id is None:
        return  # No reminder channel set

    channel = client.get_channel(reminder_channel_id)
    if channel is None:
        return

    reminder_message = (
        f"Reminder ({reminder_type}):\n"
        f"**Chore:** {chore.title}\n"
        f"**Due Date:** {chore.due_date.strftime('%d/%m/%Y')}\n"
        f"**Frequency:** {chore.repeats}/{chore.frequency.value}\n"
        f"**Assigned To:** {chore.assignee.mention if chore.assignee else 'Unassigned'}"
    )
    await channel.send(reminder_message)


async def schedule_reminders():
    """
    Schedules reminders for all chores based on their frequency and repeats.
    Sends reminders at a set time (e.g., midday).
    """
    while True:
        now = datetime.now()
        # Calculate the time until the next midday
        next_midday = datetime.combine(now.date(), datetime.min.time()) + timedelta(days=1, hours=12)
        time_until_midday = (next_midday - now).total_seconds()

        # Wait until midday
        await asyncio.sleep(time_until_midday)

        chores = DATASTORE.chores
        today = date.today()

        for chore in chores:
            if not is_chore_scheduled(chore):
                continue

            # Calculate reminder dates based on frequency and repeats
            if chore.frequency == Frequency.WEEK:
                reminder_date = chore.due_date - timedelta(days=1)
            elif chore.frequency == Frequency.MONTH:
                reminder_date = chore.due_date - timedelta(days=3)
            elif chore.frequency == Frequency.YEAR:
                reminder_date = chore.due_date - timedelta(weeks=1)

            # Send reminders
            if today == reminder_date:
                await send_reminder(chore, "Upcoming")
            if today == chore.due_date:
                await send_reminder(chore, "Due Today")
            if chore.due_date < today:
                await send_reminder(chore, "Overdue")


def is_chore_scheduled(chore: Chore) -> bool:
    """
    Checks if a chore is scheduled, i.e., has required fields: due_date, frequency, and repeats.
    """
    return all([chore.frequency, chore.repeats, chore.due_date])


def calculate_next_due_date(chore: Chore) -> date:
    """
    Calculates the next due date for a chore based on its frequency and repeats.

    Args:
        chore (Chore): The chore object.

    Returns:
        date: The next due date for the chore.
    """
    if not is_chore_scheduled(chore):
        return None

    if chore.frequency == Frequency.DAY:
        return chore.due_date + timedelta(days=1 // chore.repeats)
    elif chore.frequency == Frequency.WEEK:
        return chore.due_date + timedelta(weeks=1 // chore.repeats)
    elif chore.frequency == Frequency.MONTH:
        days_in_month = (chore.due_date.replace(day=28) + timedelta(days=4)).day
        return chore.due_date + timedelta(days=days_in_month // chore.repeats)
    elif chore.frequency == Frequency.YEAR:
        days_in_year = 366 if chore.due_date.year % 4 == 0 else 365
        return chore.due_date + timedelta(days=days_in_year // chore.repeats)

    raise ValueError(f"Invalid frequency: {chore.frequency}")


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    channel_id = DATASTORE.chore_channel_id
    if payload.channel_id != channel_id:
        return

    user_emojis = DATASTORE.user_emojis
    user_id = str(payload.user_id)
    emoji = str(payload.emoji)

    # Check if the reaction is from a valid user and emoji
    if user_id not in user_emojis or user_emojis[user_id] != emoji:
        return

    # Fetch the message and check if it corresponds to a chore
    channel = client.get_channel(channel_id)
    message = await channel.fetch_message(payload.message_id)
    chore_title = message.content.split("\n")[0].replace("**Chore:** ", "")

    chores = DATASTORE.chores
    chore = next((c for c in chores if c.title == chore_title), None)
    if not chore:
        raise ValueError(f"Chore with title '{chore_title}' not found in data.")

    next_assignee_id = next((uid for uid in user_emojis if uid != user_id), None)
    next_assignee = await client.fetch_user(next_assignee_id)
    chore.assignee = next_assignee

    # Save the updated chore
    DATASTORE.save_to_file()

    if is_chore_scheduled(chore):
        # Update the chore's due date based on frequency and repeats
        chore.due_date = calculate_next_due_date(chore)

        await message.edit(content=(
            f"**Chore:** {chore.title}\n"
            f"**Due Date:** {chore.due_date.strftime('%d/%m/%Y')}\n"
            f"**Frequency:** {chore.frequency.value}\n"
            f"**Assigned To:** {chore.assignee.mention if chore.assignee else 'Unassigned'}"
        ))
    else:
        await message.edit(content=(
            f"**Chore:** {chore.title}\n"
            f"**Assigned To:** {chore.assignee.mention if chore.assignee else 'Unassigned'}"
        ))

    # Remove the reaction to reset for the next cycle
    await message.remove_reaction(emoji, payload.member)
    await message.remove_reaction(emoji, client.user)

    # Cycle to the next user
    if next_assignee_id:
        next_emoji = user_emojis[str(next_assignee_id)]
        await message.add_reaction(next_emoji)


@tree.command(
    name="assign_chore",
    description="Assigns a chore to a specific user"
)
async def assign_chore(interaction: discord.Interaction, title: str, user: discord.User):
    # Check if the user has an emoji set
    if str(user.id) not in DATASTORE.user_emojis:
        await interaction.response.send_message(f"Error: {user.mention} does not have an emoji set. Please set an emoji first using the `/set_emoji` command.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    # Find the chore to assign
    chore = next((c for c in DATASTORE.chores if c.title == title), None)
    if chore is None:
        await interaction.response.send_message(f"Chore '{title}' not found.")
        await asyncio.sleep(10)
        await interaction.delete_original_response()
        return

    # Assign the chore to the specified user
    chore.assignee = user

    # Save the updated chores back to the datastore
    DATASTORE.save_to_file()

    # React with the assigned user's emoji
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel:
        messages = [message async for message in channel.history(limit=100)]
        for message in messages:
            if f"**Chore:** {title}" in message.content:
                updated_content = message.content.split("\n")
                for i, line in enumerate(updated_content):
                    if line.startswith("**Assigned To:**"):
                        updated_content[i] = f"**Assigned To:** {user.mention}"
                        break
                await message.edit(content="\n".join(updated_content))
                await message.clear_reactions()
                await message.add_reaction(DATASTORE.user_emojis[str(user.id)])
                break

    await interaction.response.send_message(f"Chore '{title}' has been assigned to {user.mention}.")
    await asyncio.sleep(10)
    await interaction.delete_original_response()


@client.event
async def on_message(message: discord.Message):
    # detect and delete any messages in the chore channel that are not commands
    if message.author == client.user:
        return

    if message.channel.id == DATASTORE.chore_channel_id:
        # Check if the message is a command
        if not message.content.startswith('/'):
            response = await message.channel.send("Please only use ChoreBot commands in this channel. (To keep it ✨clean✨)")
            await message.delete()
            await asyncio.sleep(10)
            await response.delete()
            return


client.run(os.getenv("DISCORD_TOKEN"))

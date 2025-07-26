import discord
from discord import app_commands
from data import FrequencyType, Schedule, Chore, Datastore
from datetime import date, datetime, timedelta
import asyncio
from emoji import is_emoji
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('chorebot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ChoreBot')


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
    logger.info(f'Logged in as {client.user}')
    try:
        # Sync the command tree with Discord
        await tree.sync()
        logger.info("Slash commands synchronized successfully.")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

    client.loop.create_task(schedule_reminders())


@tree.command(
    name="setup_channel",
    description="Sets up ChoreBot in this channel"
)
async def setup_channel(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    DATASTORE.chore_channel_id = channel_id
    DATASTORE.save_to_file()
    logger.info(f"Set up chore channel with ID: {channel_id}")

    # Delete all messages in the channel
    channel = interaction.channel
    await channel.purge()

    # Send the intro message after purging
    await interaction.response.send_message(INTRO_MESSAGE)


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
@app_commands.describe(
    title="The title of the chore",
    assignee="Who to assign the chore to",
    start_date="When the chore should start (e.g., 25/07/2025)",
    schedule="How often the chore repeats (e.g., daily, weekly, every 3 days, twice a month)"
)
async def add_chore(interaction: discord.Interaction, title: str, assignee: discord.User, start_date: str = None, schedule: str = None):
    if DATASTORE.chore_channel_id == 0:
        logger.error("Attempted to add chore before channel setup")
        await interaction.response.send_message("Must set up a channel first.")
        return
    if DATASTORE.chore_channel_id != interaction.channel_id:
        logger.warning(f"Attempted to add chore in wrong channel {interaction.channel_id}")
        await interaction.response.send_message("This is not allowed here, please run this command in your chore channel.")
        return

    # Check for duplicate chore titles
    if find_chore_by_title(title) is not None:
        logger.warning(f"Attempted to add duplicate chore: {title}")
        await interaction.response.send_message(f"A chore with the title '{title}' already exists.")
        await delete_after_delay(interaction)
        return

    # Check if the assignee has an emoji set
    if assignee and str(assignee.id) not in DATASTORE.user_emojis:
        await interaction.response.send_message(f"Error: {assignee.mention} does not have an emoji set. Please set an emoji first using the `/set_emoji` command.")
        await delete_after_delay(interaction)
        return

    # Parse schedule if provided
    chore_schedule = None
    start_date_parsed = None

    if schedule:
        try:
            chore_schedule = Schedule.from_string(schedule)
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await delete_after_delay(interaction)
            return

    if start_date:
        try:
            start_date_parsed = parse_date(start_date)
            if start_date_parsed < date.today():
                raise ValueError("Start date cannot be in the past.")
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await delete_after_delay(interaction)
            return

    # Create a new chore object
    chore = Chore(
        title=title,
        schedule=chore_schedule,
        assignee=assignee,
        due_date=start_date_parsed,
    )

    # Save the chore to the datastore
    DATASTORE.chores.append(chore)
    DATASTORE.save_to_file()
    logger.info(f"Added new chore: {title} (assigned to: {assignee.name if assignee else 'None'})")

    # Send a message to the channel with the chore details
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel is None:
        await interaction.response.send_message("Error: Could not find the channel.")
        return

    await generate_chore_message(chore, channel)
    await interaction.response.send_message(f"Chore '{title}' added successfully!")
    await delete_after_delay(interaction)


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
        logger.warning(f"Invalid emoji attempted: {emoji} by user {interaction.user.name}")
        await interaction.response.send_message("Please provide a valid single emoji.")
        await delete_after_delay(interaction)
        return

    user_id = str(interaction.user.id)
    old_emoji = DATASTORE.user_emojis.get(user_id)
    DATASTORE.user_emojis[user_id] = emoji
    DATASTORE.save_to_file()
    logger.info(f"User {interaction.user.name} changed emoji from {old_emoji if old_emoji else 'None'} to {emoji}")

    # Loop through all existing messages and change the reactions to the new emoji for this user
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel:
        messages = [message async for message in channel.history(limit=100)]
        updated_count = 0
        for message in messages:
            if any(reaction.emoji == old_emoji for reaction in message.reactions):
                # Remove the old reaction and add the new one
                await message.clear_reactions()
                await message.add_reaction(emoji)
                updated_count += 1
        if updated_count > 0:
            logger.info(f"Updated emoji reactions in {updated_count} messages")

    await interaction.response.send_message(f"Emoji set to {emoji} for you.")
    await delete_after_delay(interaction)


@tree.command(
    name="edit_chore",
    description="Edits an existing chore",
)
@app_commands.describe(
    title="The title of the chore to edit",
    new_title="New title for the chore",
    new_due_date="New due date (e.g., 25/07/2025 or 'None' to remove)",
    new_assignee="New person to assign the chore to",
    new_schedule="New schedule (e.g., daily, weekly, every 3 days, or 'None' to remove)"
)
async def edit_chore(interaction: discord.Interaction, title: str, new_title: str = None, new_due_date: str = None, new_assignee: discord.User = None, new_schedule: str = None):
    # Find the chore to edit
    chore = find_chore_by_title(title)
    if chore is None:
        logger.warning(f"Attempted to edit non-existent chore: {title}")
        await interaction.response.send_message(f"Chore '{title}' not found.")
        await delete_after_delay(interaction)
        return

    # Log the changes being made
    changes = []
    if new_title:
        changes.append(f"title: {title} -> {new_title}")
    if new_due_date:
        changes.append(f"due_date: {chore.due_date} -> {new_due_date}")
    if new_assignee:
        changes.append(f"assignee: {chore.assignee.name if chore.assignee else 'None'} -> {new_assignee.name}")
    if new_schedule:
        changes.append(f"schedule: {chore.schedule.to_string() if chore.schedule else 'None'} -> {new_schedule}")
    
    logger.info(f"Editing chore '{title}'. Changes: {', '.join(changes)}")

    # Check if the new assignee has an emoji set
    if new_assignee and str(new_assignee.id) not in DATASTORE.user_emojis:
        await interaction.response.send_message(f"Error: {new_assignee.mention} does not have an emoji set. Please set an emoji first using the `/set_emoji` command.")
        await delete_after_delay(interaction)
        return

    # Update the chore details
    if new_title:
        chore.title = new_title

    if new_due_date == "None":
        chore.due_date = None
        chore.schedule = None
    elif new_due_date:
        try:
            chore.due_date = parse_date(new_due_date)  # Validate the date format
            if chore.due_date < date.today():
                raise ValueError("Due date cannot be in the past.")
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await delete_after_delay(interaction)
            return

    if new_assignee:
        chore.assignee = new_assignee

    if new_schedule == "None":
        chore.schedule = None
    elif new_schedule:
        try:
            chore.schedule = Schedule.from_string(new_schedule)
        except ValueError as e:
            await interaction.response.send_message(f"Error: {e}")
            await delete_after_delay(interaction)
            return

    # Save the updated chores back to the datastore
    DATASTORE.save_to_file()

    # Update the message in the channel
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel:
        messages = [message async for message in channel.history(limit=100)]
        for message in messages:
            if f"**Chore:** {title}" in message.content:
                await generate_chore_message(chore, channel, message)
                break

    await interaction.response.send_message(f"Chore '{title}' updated successfully!")
    await delete_after_delay(interaction)


@tree.command(
    name="delete_chore",
    description="Deletes an existing chore",
)
async def delete_chore(interaction: discord.Interaction, title: str):
    # Find the chore to delete
    chore = find_chore_by_title(title)
    if chore is None:
        logger.warning(f"Attempted to delete non-existent chore: {title}")
        await interaction.response.send_message(f"Chore '{title}' not found.")
        await delete_after_delay(interaction)
        return

    logger.info(f"Deleting chore: {title}")
    DATASTORE.chores.remove(chore)
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
    await delete_after_delay(interaction)


@tree.command(
    name="set_reminder_channel",
    description="Sets up a separate channel for reminders"
)
async def set_reminder_channel(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    DATASTORE.reminder_channel_id = channel_id
    DATASTORE.save_to_file()
    logger.info(f"Set reminder channel to: {channel_id}")
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
        logger.warning("Attempted to send reminder but no reminder channel is set")
        return

    channel = client.get_channel(reminder_channel_id)
    if channel is None:
        logger.error(f"Could not find reminder channel with ID: {reminder_channel_id}")
        return

    logger.info(f"Sending {reminder_type} reminder for chore: {chore.title}")
    reminder_message = (
        f"Reminder ({reminder_type}):\n"
        f"**Chore:** {chore.title}\n"
        f"**Due Date:** {chore.due_date.strftime('%d/%m/%Y')}\n"
        f"**Schedule:** {chore.schedule.to_string()}\n"
        f"**Assigned To:** {chore.assignee.mention if chore.assignee else 'Unassigned'}"
    )
    await channel.send(reminder_message)


async def schedule_reminders():
    """
    Schedules reminders for all chores based on their schedule.
    Sends reminders at a set time (e.g., midday).
    """
    while True:
        now = datetime.now()
        # Calculate the time until the next midday
        next_midday = datetime.combine(now.date(), datetime.min.time()) + timedelta(days=1, hours=12)
        time_until_midday = (next_midday - now).total_seconds()

        # Wait until midday
        await asyncio.sleep(time_until_midday)

        if DATASTORE.reminder_channel_id is None:
            logger.warning("No reminder channel set. Skipping reminders.")
            continue

        chores = DATASTORE.chores
        today = date.today()

        for chore in chores:
            if not is_chore_scheduled(chore):
                continue

            # Calculate reminder dates based on schedule
            if chore.schedule.frequency_type == FrequencyType.WEEKLY:
                reminder_date = chore.due_date - timedelta(days=1)
            elif chore.schedule.frequency_type == FrequencyType.MONTHLY:
                reminder_date = chore.due_date - timedelta(days=3)
            elif chore.schedule.frequency_type == FrequencyType.YEARLY:
                reminder_date = chore.due_date - timedelta(weeks=1)
            else:
                # For daily and X-day intervals, remind on the due date
                reminder_date = chore.due_date

            # Send reminders
            if today == reminder_date:
                await send_reminder(chore, "Upcoming")
            if today == chore.due_date:
                await send_reminder(chore, "Due Today")
            if chore.due_date < today:
                await send_reminder(chore, "Overdue")


def is_chore_scheduled(chore: Chore) -> bool:
    """
    Checks if a chore is scheduled, i.e., has required fields: due_date and schedule.
    """
    return bool(chore.schedule and chore.due_date)


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
        logger.warning(f"Invalid reaction from user {user_id} with emoji {emoji}")
        return

    # Fetch the message and check if it corresponds to a chore
    channel = client.get_channel(channel_id)
    message = await channel.fetch_message(payload.message_id)
    chore_title = message.content.split("\n")[0].replace("**Chore:** ", "")
    
    chore = find_chore_by_title(chore_title)
    if not chore:
        error_msg = f"Chore with title '{chore_title}' not found in data."
        logger.error(error_msg)
        raise ValueError(error_msg)

    next_assignee_id = next((uid for uid in user_emojis if uid != user_id), None)
    next_assignee = await client.fetch_user(next_assignee_id)
    chore.assignee = next_assignee
    logger.info(f"Chore '{chore.title}' reassigned to {next_assignee.name if next_assignee else 'None'}")

    # Save the updated chore
    DATASTORE.save_to_file()

    if is_chore_scheduled(chore):
        # Update the chore's due date based on schedule
        chore.due_date = chore.schedule.calculate_next_date(chore.due_date)
    
    # Update message in-place
    await generate_chore_message(chore, channel, message)


@tree.command(
    name="assign_chore",
    description="Assigns a chore to a specific user"
)
async def assign_chore(interaction: discord.Interaction, title: str, user: discord.User):
    # Check if the user has an emoji set
    if str(user.id) not in DATASTORE.user_emojis:
        await interaction.response.send_message(f"Error: {user.mention} does not have an emoji set. Please set an emoji first using the `/set_emoji` command.")
        await delete_after_delay(interaction)
        return

    # Find the chore to assign
    chore = find_chore_by_title(title)
    if chore is None:
        await interaction.response.send_message(f"Chore '{title}' not found.")
        await delete_after_delay(interaction)
        return

    # Assign the chore to the specified user
    chore.assignee = user

    # Save the updated chores back to the datastore
    DATASTORE.save_to_file()

    # Update the message in the channel
    channel = client.get_channel(DATASTORE.chore_channel_id)
    if channel:
        messages = [message async for message in channel.history(limit=100)]
        for message in messages:
            if f"**Chore:** {title}" in message.content:
                await generate_chore_message(chore, channel, message)
                break

    await interaction.response.send_message(f"Chore '{title}' has been assigned to {user.mention}.")
    await delete_after_delay(interaction)


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
            await delete_after_delay(response)
            return


def find_chore_by_title(title: str) -> Chore | None:
    """
    Finds a chore by its title in a case-insensitive way.

    Args:
        title (str): The title of the chore to find.

    Returns:
        Chore | None: The found chore or None if not found.
    """
    return next((c for c in DATASTORE.chores if c.title.lower() == title.lower()), None)


async def delete_after_delay(interaction_or_message, delay_seconds: int = 10):
    """
    Waits for the specified delay and then deletes the message.
    
    Args:
        interaction_or_message: Either a discord.Interaction or discord.Message
        delay_seconds: Number of seconds to wait before deleting. Defaults to 10.
    """
    await asyncio.sleep(delay_seconds)
    
    if isinstance(interaction_or_message, discord.Interaction):
        try:
            await interaction_or_message.delete_original_response()
        except discord.NotFound:
            logger.debug("Interaction response already deleted")
            pass
    else:
        try:
            await interaction_or_message.delete()
        except (discord.NotFound, AttributeError):
            logger.debug("Message already deleted or doesn't support deletion")
            pass


async def generate_chore_message(chore: Chore, channel: discord.TextChannel, existing_message: discord.Message = None) -> discord.Message:
    """
    Generates or updates a message for a chore in the specified channel.
    Also adds the appropriate reaction emoji.
    
    Args:
        chore: The chore to generate a message for
        channel: The channel to send the message in
        existing_message: Optional existing message to update instead of creating a new one
        
    Returns:
        The sent or updated message
    """
    message_content = f"**Chore:** {chore.title}\n"
    if chore.due_date:
        message_content += f"**Due Date:** {chore.due_date.strftime('%d/%m/%Y')}\n"
    if chore.schedule:
        message_content += f"**Schedule:** {chore.schedule.to_string()}\n"
    message_content += f"**Assigned To:** {chore.assignee.mention if chore.assignee else 'Unassigned'}"

    if existing_message:
        message = await existing_message.edit(content=message_content)
        await existing_message.clear_reactions()
    else:
        message = await channel.send(message_content)
    
    if chore.assignee and str(chore.assignee.id) in DATASTORE.user_emojis:
        await message.add_reaction(DATASTORE.user_emojis[str(chore.assignee.id)])
    
    return message

@tree.command(
    name="regenerate_messages",
    description="Regenerates all chore messages in the channel"
)
async def regenerate_messages(interaction: discord.Interaction):
    """Regenerates all chore messages in the channel"""
    if DATASTORE.chore_channel_id == 0 or DATASTORE.chore_channel_id != interaction.channel_id:
        await interaction.response.send_message("This command must be run in the chore channel.")
        await delete_after_delay(interaction)
        return
        
    # Delete all existing messages
    channel = interaction.channel
    await channel.purge()
    
    # Send the intro message first
    await channel.send(INTRO_MESSAGE)
    
    # Regenerate messages for all chores
    regenerated_count = 0
    for chore in DATASTORE.chores:
        try:
            await generate_chore_message(chore, channel)
            regenerated_count += 1
        except Exception as e:
            logger.error(f"Failed to regenerate message for chore '{chore.title}': {e}")
    
    logger.info(f"Regenerated {regenerated_count} chore messages")
    await interaction.response.send_message(f"Successfully regenerated {regenerated_count} chore messages!")
    await delete_after_delay(interaction)


client.run(os.getenv("DISCORD_TOKEN"))

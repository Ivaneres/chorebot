from __future__ import annotations
from enum import Enum
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import discord
import logging

logger = logging.getLogger('ChoreBot')


class FrequencyType(Enum):
    DAILY = "daily"
    DAYS = "days"
    WEEKLY = "weekly"
    MONTHLY = "months"
    YEARLY = "years"


@dataclass
class Schedule:
    """Represents a chore's schedule configuration"""
    frequency_type: FrequencyType
    interval: int = 1  # e.g., 1 for daily, 2 for every other day, etc.

    @staticmethod
    def from_string(schedule_str: str) -> Schedule:
        """Creates a Schedule from a user-friendly string"""
        schedule_str = schedule_str.lower().strip()
        
        if schedule_str == "daily":
            return Schedule(FrequencyType.DAILY)
        elif schedule_str == "weekly":
            return Schedule(FrequencyType.WEEKLY)
        elif schedule_str == "twice a week":
            return Schedule(FrequencyType.DAYS, 3)  # Every 3 days approximately twice a week
        elif schedule_str == "every other day":
            return Schedule(FrequencyType.DAYS, 2)
        elif schedule_str == "twice a month":
            return Schedule(FrequencyType.DAYS, 15)  # Every 15 days
        elif schedule_str == "monthly":
            return Schedule(FrequencyType.MONTHLY, 1)
        elif schedule_str == "yearly":
            return Schedule(FrequencyType.YEARLY, 1)
        
        # Handle "every X days/months/years" format
        if schedule_str.startswith("every "):
            parts = schedule_str.split()
            if len(parts) >= 3:
                try:
                    interval = int(parts[1])
                    unit = parts[2].rstrip('s')  # Remove plural 's' if present
                    
                    if unit == "day":
                        return Schedule(FrequencyType.DAYS, interval)
                    elif unit == "month":
                        return Schedule(FrequencyType.MONTHLY, interval)
                    elif unit == "year":
                        return Schedule(FrequencyType.YEARLY, interval)
                except ValueError:
                    pass
        
        raise ValueError(
            "Invalid schedule format. Valid formats:\n"
            "- daily\n"
            "- every other day\n"
            "- every X days\n"
            "- weekly\n"
            "- twice a week\n"
            "- monthly\n"
            "- twice a month\n"
            "- every X months\n"
            "- yearly\n"
            "- every X years"
        )

    def to_string(self) -> str:
        """Converts the schedule to a user-friendly string"""
        if self.frequency_type == FrequencyType.DAILY and self.interval == 1:
            return "daily"
        elif self.frequency_type == FrequencyType.WEEKLY and self.interval == 1:
            return "weekly"
        elif self.frequency_type == FrequencyType.DAYS and self.interval == 2:
            return "every other day"
        elif self.frequency_type == FrequencyType.DAYS and self.interval == 3:
            return "twice a week"
        elif self.frequency_type == FrequencyType.DAYS and self.interval == 15:
            return "twice a month"
        elif self.frequency_type == FrequencyType.MONTHLY and self.interval == 1:
            return "monthly"
        elif self.frequency_type == FrequencyType.YEARLY and self.interval == 1:
            return "yearly"
        
        unit = self.frequency_type.value
        return f"every {self.interval} {unit}"

    def calculate_next_date(self, from_date: date) -> date:
        """Calculates the next due date based on the schedule"""
        if self.frequency_type == FrequencyType.DAILY:
            return from_date + timedelta(days=1)
        elif self.frequency_type == FrequencyType.DAYS:
            return from_date + timedelta(days=self.interval)
        elif self.frequency_type == FrequencyType.WEEKLY:
            return from_date + timedelta(weeks=self.interval)
        elif self.frequency_type == FrequencyType.MONTHLY:
            # Add months by calculating days, handling month length variations
            new_month = from_date.month - 1 + self.interval
            new_year = from_date.year + new_month // 12
            new_month = new_month % 12 + 1
            return date(new_year, new_month, from_date.day)
        elif self.frequency_type == FrequencyType.YEARLY:
            return date(from_date.year + self.interval, from_date.month, from_date.day)
        
        raise ValueError(f"Invalid frequency type: {self.frequency_type}")


@dataclass
class Chore:
    title: str
    schedule: Schedule | None
    assignee: discord.User | None
    due_date: date | None

    @staticmethod
    async def from_dict(data: dict, client: discord.Client) -> Chore:
        schedule = None
        if data.get("schedule"):
            schedule = Schedule(
                FrequencyType[data["schedule"]["frequency_type"]],
                data["schedule"]["interval"]
            )
            
        assignee = None
        if data.get("assignee"):
            try:
                assignee = await client.fetch_user(int(data["assignee"]))
            except discord.NotFound:
                logger.warning(f"Could not find user with ID {data['assignee']}")
            
        return Chore(
            title=data["title"],
            schedule=schedule,
            assignee=assignee,
            due_date=date.fromisoformat(data["due_date"]) if data.get("due_date") else None
        )
    
    def to_json(self):
        return {
            "title": self.title,
            "schedule": {
                "frequency_type": self.schedule.frequency_type.name,
                "interval": self.schedule.interval
            } if self.schedule else None,
            "assignee": str(self.assignee.id) if self.assignee else None,
            "due_date": self.due_date.isoformat() if self.due_date else None
        }


@dataclass
class Datastore:
    chores: list[Chore]
    chore_channel_id: int | None
    reminder_channel_id: int | None
    user_emojis: dict[str, str]
    filepath: Path

    @staticmethod
    async def load_from_file(file_path_str: str, client: discord.Client) -> Datastore:
        filepath = Path(file_path_str)
        if not filepath.exists():
            raise FileNotFoundError(f"Data file not found: {filepath}")

        with open(filepath, "r") as file:
            data = json.load(file)

        # Load chores asynchronously
        chores = []
        for chore_data in data.get("chores", []):
            chore = await Chore.from_dict(chore_data, client)
            chores.append(chore)

        return Datastore(
            chores=chores,
            chore_channel_id=data.get("chore_channel_id"),
            reminder_channel_id=data.get("reminder_channel_id"),
            user_emojis=data.get("user_emojis", {}),
            filepath=filepath
        )
    
    def save_to_file(self):
        data = {
            "chores": [chore.to_json() for chore in self.chores],
            "chore_channel_id": self.chore_channel_id,
            "reminder_channel_id": self.reminder_channel_id,
            "user_emojis": self.user_emojis
        }
        
        with open(self.filepath, "w") as file:
            json.dump(data, file, indent=4)

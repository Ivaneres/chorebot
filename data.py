from __future__ import annotations
from enum import Enum
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import discord


class Frequency(Enum):
    DAY = "Daily"
    WEEK = "Weekly"
    MONTH = "Monthly"
    YEAR = "Yearly"

    def from_value(value: str):
        for freq in Frequency:
            if freq.value.lower() == value.lower():
                return freq
        raise ValueError(f"Invalid frequency value: {value}")


@dataclass
class Chore:
    title: str
    repeats: int | None
    frequency: Frequency | None
    assignee: discord.User | None
    due_date: date | None

    def from_dict(data: dict, client: discord.Client) -> Chore:
        return Chore(
            title=data["title"],
            repeats=data["repeats"],
            frequency=Frequency[data["frequency"]] if data.get("frequency") else None,
            assignee=discord.utils.get(client.get_all_members(), id=int(data["assignee"])) if data.get("assignee") else None,
            due_date=date.fromisoformat(data["due_date"]) if data.get("due_date") else None
        )
    
    def to_json(self):
        return {
            "title": self.title,
            "repeats": self.repeats,
            "frequency": self.frequency.name if self.frequency else None,
            "assignee": str(self.assignee.id) if self.assignee else None,
            "due_date": self.due_date.isoformat() if self.due_date else None
        }


@dataclass
class Datastore:
    chores: list[Chore]
    chore_channel_id: int
    user_emojis: dict[str, str]
    filepath: Path

    def load_from_file(file_path_str: str, client: discord.Client) -> Datastore:
        filepath = Path(file_path_str)
        if not filepath.exists():
            raise FileNotFoundError(f"Data file not found: {filepath}")

        with open(filepath, "r") as file:
            data = json.load(file)

        chores = [Chore.from_dict(chore, client) for chore in data.get("chores", [])]
        return Datastore(
            chores=chores,
            chore_channel_id=data.get("chore_channel_id", 0),
            user_emojis=data.get("user_emojis", {}),
            filepath=filepath
        )
    
    def save_to_file(self):
        data = {
            "chores": [chore.to_json() for chore in self.chores],
            "chore_channel_id": self.chore_channel_id,
            "user_emojis": self.user_emojis
        }
        
        with open(self.filepath, "w") as file:
            json.dump(data, file, indent=4)

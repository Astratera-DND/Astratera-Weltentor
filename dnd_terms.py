"""
Ready-made word lists for the Transcribe tool: D&D names and terms that speech recognition often gets wrong.

Only NAMES and TERMS are listed (no descriptions or rules text). Most English entries are names from the
System Reference Document 5.1 by Wizards of the Coast LLC, which is available under the Creative Commons
Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0/legalcode). A few well-known
extras (for example Beholder, Mind Flayer, Strahd) are common table vocabulary; they are used here only as words.
"""

# (key, language, label, words)
CATEGORIES = [
    ("monsters", "English", "Monsters", [
        "Aboleth", "Air Elemental", "Animated Armor", "Ankheg", "Azer", "Balor", "Basilisk", "Bearded Devil", "Behir",
        "Beholder", "Black Pudding", "Blink Dog", "Bone Devil", "Bugbear", "Bulette", "Centaur", "Chain Devil", "Chimera",
        "Chuul", "Cloaker", "Cockatrice", "Couatl", "Darkmantle", "Death Knight", "Death Dog", "Deva", "Dire Wolf",
        "Displacer Beast", "Doppelganger", "Dragon Turtle", "Dretch", "Drider", "Dryad", "Duergar", "Dust Mephit",
        "Efreeti", "Erinyes", "Ettercap", "Ettin", "Fire Giant", "Flameskull", "Flesh Golem", "Frost Giant", "Gargoyle",
        "Gelatinous Cube", "Ghast", "Ghoul", "Gibbering Mouther", "Glabrezu", "Gnoll", "Goblin", "Gorgon", "Gray Ooze",
        "Green Hag", "Grick", "Griffon", "Grimlock", "Hell Hound", "Hezrou", "Hill Giant", "Hippogriff", "Hobgoblin",
        "Homunculus", "Hook Horror", "Hydra", "Illithid", "Imp", "Invisible Stalker", "Iron Golem", "Kobold", "Kraken",
        "Lamia", "Lemure", "Lich", "Lizardfolk", "Manticore", "Marilith", "Medusa", "Mimic", "Mind Flayer", "Minotaur",
        "Mummy", "Mummy Lord", "Nalfeshnee", "Night Hag", "Nightmare", "Ochre Jelly", "Ogre", "Oni", "Otyugh", "Owlbear",
        "Pegasus", "Phase Spider", "Pit Fiend", "Pixie", "Planetar", "Purple Worm", "Quasit", "Rakshasa", "Remorhaz",
        "Roc", "Roper", "Rust Monster", "Sahuagin", "Salamander", "Satyr", "Shadow Demon", "Shambling Mound",
        "Shield Guardian", "Specter", "Sphinx", "Sprite", "Stirge", "Succubus", "Tarrasque", "Treant", "Troll",
        "Umber Hulk", "Unicorn", "Vampire", "Vampire Spawn", "Werebear", "Wererat", "Weretiger", "Werewolf", "Wight",
        "Will-o'-Wisp", "Wraith", "Wyvern", "Xorn", "Yeti", "Red Dragon", "Blue Dragon", "Green Dragon", "Black Dragon",
        "White Dragon", "Brass Dragon", "Bronze Dragon", "Copper Dragon", "Gold Dragon", "Silver Dragon",
    ]),
    ("spells", "English", "Spells", [
        "Acid Arrow", "Animate Dead", "Animate Objects", "Antimagic Field", "Arcane Eye", "Arcane Lock", "Astral Projection",
        "Augury", "Banishment", "Barkskin", "Beacon of Hope", "Bestow Curse", "Blade Barrier", "Blight", "Burning Hands",
        "Call Lightning", "Calm Emotions", "Chain Lightning", "Charm Person", "Chill Touch", "Circle of Death",
        "Clairvoyance", "Cloudkill", "Color Spray", "Comprehend Languages", "Cone of Cold", "Conjure Animals",
        "Conjure Elemental", "Contagion", "Contingency", "Counterspell", "Create Food and Water", "Create Undead",
        "Cure Wounds", "Dancing Lights", "Darkvision", "Daylight", "Death Ward", "Delayed Blast Fireball", "Detect Magic",
        "Detect Thoughts", "Dimension Door", "Disguise Self", "Disintegrate", "Dispel Magic", "Dissonant Whispers",
        "Divination", "Divine Favor", "Divine Word", "Dominate Monster", "Dominate Person", "Druidcraft", "Earthquake",
        "Eldritch Blast", "Enlarge/Reduce", "Entangle", "Etherealness", "Faerie Fire", "False Life", "Feather Fall",
        "Feeblemind", "Find Familiar", "Finger of Death", "Fire Bolt", "Fire Shield", "Fire Storm", "Fireball",
        "Flame Blade", "Flame Strike", "Flaming Sphere", "Flesh to Stone", "Fog Cloud", "Forcecage", "Foresight",
        "Freedom of Movement", "Gaseous Form", "Geas", "Gentle Repose", "Giant Insect", "Glibness",
        "Globe of Invulnerability", "Greater Invisibility", "Greater Restoration", "Guardian of Faith", "Guiding Bolt",
        "Gust of Wind", "Healing Word", "Heat Metal", "Hellish Rebuke", "Heroes' Feast", "Heroism", "Hold Monster",
        "Hold Person", "Holy Aura", "Hunter's Mark", "Hypnotic Pattern", "Ice Storm", "Illusory Script", "Imprisonment",
        "Inflict Wounds", "Insect Plague", "Invisibility", "Legend Lore", "Lesser Restoration", "Levitate",
        "Lightning Bolt", "Locate Creature", "Locate Object", "Longstrider", "Mage Armor", "Mage Hand", "Magic Circle",
        "Magic Jar", "Magic Missile", "Magic Mouth", "Magic Weapon", "Major Image", "Mass Cure Wounds", "Mass Heal",
        "Mass Healing Word", "Mass Suggestion", "Meld into Stone", "Meteor Swarm", "Mind Blank", "Minor Illusion",
        "Mirage Arcane", "Mirror Image", "Mislead", "Misty Step", "Modify Memory", "Moonbeam", "Move Earth",
        "Nondetection", "Pass without Trace", "Passwall", "Phantasmal Killer", "Planar Ally", "Planar Binding",
        "Plane Shift", "Plant Growth", "Poison Spray", "Polymorph", "Power Word Heal", "Power Word Kill",
        "Power Word Stun", "Prayer of Healing", "Prestidigitation", "Prismatic Spray", "Prismatic Wall", "Produce Flame",
        "Programmed Illusion", "Project Image", "Protection from Energy", "Protection from Evil and Good", "Raise Dead",
        "Ray of Frost", "Ray of Sickness", "Regenerate", "Reincarnate", "Remove Curse", "Resurrection", "Reverse Gravity",
        "Revivify", "Rope Trick", "Sacred Flame", "Sanctuary", "Scorching Ray", "Scrying", "Searing Smite",
        "See Invisibility", "Shapechange", "Shatter", "Shield of Faith", "Shillelagh", "Shocking Grasp", "Silent Image",
        "Simulacrum", "Sleet Storm", "Spare the Dying", "Speak with Animals", "Speak with Dead", "Speak with Plants",
        "Spider Climb", "Spirit Guardians", "Spiritual Weapon", "Stinking Cloud", "Stone Shape", "Stoneskin",
        "Storm of Vengeance", "Suggestion", "Sunbeam", "Sunburst", "Telekinesis", "Telepathy", "Teleportation Circle",
        "Thaumaturgy", "Thunderwave", "Time Stop", "Tongues", "Transport via Plants", "Tree Stride", "True Polymorph",
        "True Resurrection", "True Seeing", "True Strike", "Unseen Servant", "Vampiric Touch", "Vicious Mockery",
        "Wall of Fire", "Wall of Force", "Wall of Ice", "Wall of Stone", "Wall of Thorns", "Warding Bond",
        "Water Breathing", "Water Walk", "Wind Walk", "Wind Wall", "Witch Bolt", "Word of Recall", "Zone of Truth",
    ]),
    ("people", "English", "Races, classes and subclasses", [
        "Dragonborn", "Dwarf", "Gnome", "Half-Elf", "Half-Orc", "Halfling", "Tiefling", "Aasimar", "Genasi", "Goliath",
        "Tabaxi", "Kenku", "Aarakocra", "Firbolg", "Triton", "Tortle", "Warforged", "Changeling", "Shifter", "Kalashtar",
        "Githyanki", "Githzerai", "Drow", "Svirfneblin", "Artificer", "Barbarian", "Bard", "Cleric", "Druid", "Fighter",
        "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard", "Eldritch Knight", "Arcane Trickster",
        "Battle Master", "Path of the Berserker", "Wild Magic", "Draconic Bloodline", "Circle of the Moon",
        "Circle of the Land", "Way of the Open Hand", "Oath of Devotion", "Oath of Vengeance", "Oath of the Ancients",
        "Beast Master", "Archfey", "Great Old One", "Evocation", "Abjuration", "Conjuration", "Enchantment",
        "Necromancy", "Transmutation",
    ]),
    ("rules", "English", "Rules, conditions and skills", [
        "Advantage", "Disadvantage", "Saving Throw", "Armor Class", "Hit Points", "Initiative", "Proficiency Bonus",
        "Inspiration", "Short Rest", "Long Rest", "Death Saving Throw", "Opportunity Attack", "Concentration",
        "Spell Slot", "Cantrip", "Critical Hit", "Dungeon Master", "Challenge Rating", "Bonus Action", "Legendary Action",
        "Lair Action", "Temporary Hit Points", "Attunement", "Sneak Attack", "Wild Shape", "Channel Divinity",
        "Lay on Hands", "Bardic Inspiration", "Action Surge", "Second Wind", "Divine Smite", "Sorcery Points",
        "Blinded", "Charmed", "Deafened", "Frightened", "Grappled", "Incapacitated", "Paralyzed", "Petrified", "Poisoned",
        "Restrained", "Stunned", "Unconscious", "Exhaustion", "Acrobatics", "Animal Handling", "Arcana", "Athletics",
        "Deception", "Insight", "Intimidation", "Investigation", "Medicine", "Perception", "Persuasion",
        "Sleight of Hand", "Stealth", "Survival", "Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom",
        "Charisma", "Bludgeoning", "Piercing", "Slashing", "Necrotic", "Radiant",
    ]),
    ("items", "English", "Magic items", [
        "Bag of Holding", "Cloak of Elvenkind", "Cloak of Protection", "Boots of Speed", "Boots of Elvenkind",
        "Ring of Protection", "Ring of Invisibility", "Wand of Magic Missiles", "Wand of Fireballs", "Staff of Power",
        "Staff of the Magi", "Vorpal Sword", "Flame Tongue", "Frost Brand", "Sun Blade", "Holy Avenger", "Dragon Slayer",
        "Amulet of Health", "Belt of Giant Strength", "Gauntlets of Ogre Power", "Headband of Intellect",
        "Helm of Brilliance", "Portable Hole", "Rod of Lordly Might", "Sphere of Annihilation", "Deck of Many Things",
        "Immovable Rod", "Eversmoking Bottle", "Potion of Healing", "Efreeti Bottle", "Cube of Force", "Crystal Ball",
        "Iron Flask", "Instant Fortress", "Animated Shield", "Spellguard Shield", "Mithral", "Adamantine",
        "Thieves' Tools", "Component Pouch", "Arcane Focus",
    ]),
    ("places", "English", "Planes, worlds and famous names", [
        "Feywild", "Shadowfell", "Material Plane", "Ethereal Plane", "Astral Plane", "Nine Hells", "Abyss",
        "Mount Celestia", "Elysium", "Arborea", "Ysgard", "Limbo", "Pandemonium", "Carceri", "Gehenna", "Acheron",
        "Mechanus", "Bytopia", "Beastlands", "Sigil", "Underdark", "Neverwinter", "Waterdeep", "Baldur's Gate", "Faerûn",
        "Sword Coast", "Forgotten Realms", "Eberron", "Ravenloft", "Barovia", "Greyhawk", "Dragonlance", "Krynn", "Sharn",
        "Tiamat", "Bahamut", "Asmodeus", "Lolth", "Moradin", "Corellon", "Pelor", "Bane", "Bhaal", "Mystra", "Tymora",
        "Lathander", "Gruumsh", "Vecna", "Orcus", "Demogorgon", "Graz'zt", "Zariel", "Acererak", "Strahd",
    ]),
    ("de_rules", "German", "Regeln, Zustände und Klassen", [
        "Rettungswurf", "Rüstungsklasse", "Trefferpunkte", "Initiative", "Vorteil", "Nachteil", "Zauberplatz", "Zaubertrick",
        "Konzentration", "Kurze Rast", "Lange Rast", "Meisterschaftsbonus", "Todesrettungswurf", "Gelegenheitsangriff",
        "Bonusaktion", "Herausforderungsgrad", "Spielleiter", "Fertigkeitswurf", "Geschicklichkeit", "Stärke",
        "Konstitution", "Intelligenz", "Weisheit", "Charisma", "Barbar", "Barde", "Kleriker", "Druide", "Kämpfer",
        "Mönch", "Paladin", "Waldläufer", "Schurke", "Zauberer", "Hexenmeister", "Magier", "Drachenblütiger", "Zwerg",
        "Gnom", "Halbelf", "Halbork", "Halbling", "Tiefling", "Geblendet", "Bezaubert", "Verängstigt", "Gepackt",
        "Kampfunfähig", "Gelähmt", "Versteinert", "Vergiftet", "Festgesetzt", "Betäubt", "Bewusstlos", "Erschöpfung",
    ]),
    ("de_monsters", "German", "Monster", [
        "Eulenbär", "Rostmonster", "Gallertwürfel", "Gedankenschinder", "Betrachter", "Kobold", "Goblin", "Hobgoblin",
        "Gnoll", "Oger", "Troll", "Drache", "Lich", "Vampir", "Werwolf", "Zombie", "Skelett", "Gargyle", "Golem", "Mumie",
        "Kraken", "Hydra", "Greif", "Einhorn", "Zentaur", "Minotaurus", "Medusa", "Chimäre", "Basilisk", "Wyvern",
        "Rakshasa", "Sukkubus", "Höllenhund", "Todesritter", "Doppelgänger", "Aboleth", "Bulette", "Ghul", "Wiedergänger",
    ]),
]


def categories():
    return CATEGORIES


def words_for(keys):
    out = []
    for key, _lang, _label, words in CATEGORIES:
        if key in keys:
            out += words
    return out

"""A display name nobody typed: « Renard-Bleu-42 ».

An account made at the token door (codes.py, PLAN.md §59) carries no personal
data, and a name box is where a lycéen types their own. So the platform picks
the name, and nothing lets the account change it.

An animal, a colour, a number. Enough to tell two rows of a scoreboard apart
and to say "c'est moi, Renard-Bleu-42" to the person at the next desk, and
nothing a person could be found by.

**These are French words and they are not translated.** A name is stored once
and read by everybody, whatever language their page is in: it is data, like
the workshop's content, not interface. The audience is French lycéens.

What is in the lists is chosen, not collected:

  * every animal is a masculine noun and every colour reads right after one,
    so no pair needs agreement (« Panthère-Bleu » would be wrong);
  * no animal that is also an insult in a classroom (pigeon, blaireau, âne,
    cochon), and no colour that names a skin (noir, blanc, jaune, marron):
    the pair is drawn at random and lands on a teenager.
"""
import secrets

ANIMALS = (
    "Aigle", "Bison", "Castor", "Chamois", "Colibri", "Corbeau", "Crabe",
    "Cygne", "Dauphin", "Dragon", "Écureuil", "Faucon", "Gecko", "Griffon",
    "Guépard", "Hérisson", "Héron", "Hibou", "Homard", "Jaguar", "Koala",
    "Lama", "Lapin", "Lézard", "Lièvre", "Lion", "Loup", "Lynx", "Narval",
    "Okapi", "Ours", "Panda", "Papillon", "Phénix", "Pingouin", "Poulpe",
    "Puma", "Renard", "Requin", "Scarabée", "Tapir", "Tigre", "Toucan",
    "Wombat", "Yéti", "Zèbre",
)
COLOURS = (
    "Ambre", "Argenté", "Azur", "Bleu", "Corail", "Cyan", "Doré", "Émeraude",
    "Indigo", "Lilas", "Magenta", "Orange", "Rose", "Rouge", "Turquoise",
    "Vert", "Violet",
)


def new_name(wide=False):
    """One name. `wide` draws the number from 100 to 9999 instead of 10 to 99:
    the caller asks for it after a few collisions, so a busy instance gets
    longer numbers rather than a loop that never ends."""
    number = (secrets.randbelow(9900) + 100) if wide else (secrets.randbelow(90) + 10)
    return f"{secrets.choice(ANIMALS)}-{secrets.choice(COLOURS)}-{number}"

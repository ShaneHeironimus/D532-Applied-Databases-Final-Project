import pandas as pd
import sqlite3

# reading in the shelter data CSV file
shelter_data = pd.read_csv('Animal_Shelter_Animals_20260615.csv')

# cleaning the columns that will be used for the dashboard
text_cols = ['speciesname', 'breedname', 'basecolour', 'sexname', 'animalname', 'animalage', 'movementtype']
for col in text_cols:
    if col in shelter_data.columns:
        shelter_data[col] = shelter_data[col].astype(str).str.strip()
        shelter_data.loc[shelter_data[col].isin(['nan', 'None', '']), col] = None

# ensuring empty cells for the breed name are updated to 'Unknown' and changing puttosleep to a binary 0 or 1
shelter_data['breedname'] = shelter_data['breedname'].fillna('Unknown')
shelter_data['puttosleep'] = pd.to_numeric(shelter_data['puttosleep']).fillna(0).astype(int)

# creating and connecting to the production database
conn = sqlite3.connect('prod_shelter.db')
cursor = conn.cursor()

# this is required when using foreign keys with SQLite
cursor.execute("PRAGMA foreign_keys = ON;")

# normalizing the database
# testing creation and insertions, will remove later
cursor.executescript("""
    DROP TABLE IF EXISTS animals;
    DROP TABLE IF EXISTS breeds;
    DROP TABLE IF EXISTS species;
    DROP TABLE IF EXISTS colors;
    DROP TABLE IF EXISTS sexes;""")

cursor.execute("""
    CREATE TABLE species (
        species_id INTEGER PRIMARY KEY AUTOINCREMENT,
        species_name TEXT NOT NULL UNIQUE);""")

cursor.execute("""
    CREATE TABLE breeds (
        breed_id INTEGER PRIMARY KEY AUTOINCREMENT,
        breed_name TEXT NOT NULL,
        species_id INTEGER NOT NULL,
        FOREIGN KEY (species_id) REFERENCES species(species_id),
        UNIQUE(breed_name, species_id));""")

cursor.execute("""
    CREATE TABLE colors (
        color_id INTEGER PRIMARY KEY AUTOINCREMENT,
        color_name TEXT NOT NULL UNIQUE);""")

cursor.execute("""
    CREATE TABLE sexes (
        sex_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sex_name TEXT NOT NULL UNIQUE);""")

cursor.execute("""
    CREATE TABLE animals (
        unique_id INTEGER PRIMARY KEY AUTOINCREMENT,
        animal_id TEXT NOT NULL,
        animal_name TEXT,
        intake_date TEXT,
        animal_age TEXT,
        species_id INTEGER,
        breed_id INTEGER,
        color_id INTEGER,
        sex_id INTEGER,
        movementtype TEXT,
        puttosleep INTEGER,
        deceaseddate TEXT,
        FOREIGN KEY (species_id) REFERENCES species(species_id),
        FOREIGN KEY (breed_id) REFERENCES breeds(breed_id),
        FOREIGN KEY (color_id) REFERENCES colors(color_id),
        FOREIGN KEY (sex_id) REFERENCES sexes(sex_id));""")

conn.commit()

# populating the new tables with their respective data

# species
distinct_species  = []
for species in shelter_data['speciesname'].dropna().unique():
    distinct_species.append(species)
distinct_species.sort()

pd.DataFrame({'species_name': distinct_species}).to_sql('species', conn, if_exists='append', index=False)
species_map = pd.read_sql("SELECT species_id, species_name FROM species", conn).set_index('species_name')['species_id'].to_dict()

# breeds
distinct_breeds = shelter_data[['breedname', 'speciesname']].dropna().drop_duplicates()
breeds_to_load = pd.DataFrame({
    'breed_name': distinct_breeds['breedname'],
    'species_id': distinct_breeds['speciesname'].map(species_map)
}).dropna().sort_values(by='breed_name')
breeds_to_load.to_sql('breeds', conn, if_exists='append', index=False)

# create a dictionary mapping for breeds: breed_name and species_id to breed_id
breed_query = pd.read_sql("SELECT breed_id, breed_name, species_id FROM breeds", conn)
breed_map = breed_query.set_index(['breed_name', 'species_id'])['breed_id'].to_dict()

# colors
distinct_colors = []
for color in shelter_data['basecolour'].dropna().unique():
    distinct_colors.append(color)
distinct_colors.sort()

pd.DataFrame({'color_name': distinct_colors}).to_sql('colors', conn, if_exists='append', index=False)
color_map = pd.read_sql("SELECT color_id, color_name FROM colors", conn).set_index('color_name')['color_id'].to_dict()

# sexes
distinct_sexes = []
for sex in shelter_data['sexname'].dropna().unique():
    distinct_sexes.append(sex)
distinct_sexes.sort()

pd.DataFrame({'sex_name': distinct_sexes}).to_sql('sexes', conn, if_exists='append', index=False)
sex_map = pd.read_sql("SELECT sex_id, sex_name FROM sexes", conn).set_index('sex_name')['sex_id'].to_dict()

# map text strings directly to their newly generated relational IDs
shelter_data['mapped_species_id'] = shelter_data['speciesname'].map(species_map)

# multiple key map lookup for breeds to preserve strict species pairings
shelter_data['mapped_breed_id'] = shelter_data.set_index(['breedname', 'mapped_species_id']).index.map(breed_map)
shelter_data['mapped_color_id'] = shelter_data['basecolour'].map(color_map)
shelter_data['mapped_sex_id'] = shelter_data['sexname'].map(sex_map)

# creating the dataframe to insert the actual data
final_animals_df = pd.DataFrame({
    'animal_id': shelter_data['id'],
    'animal_name': shelter_data['animalname'],
    'intake_date': shelter_data['intakedate'],
    'animal_age': shelter_data['animalage'],
    'species_id': shelter_data['mapped_species_id'],
    'breed_id': shelter_data['mapped_breed_id'],
    'color_id': shelter_data['mapped_color_id'],
    'sex_id': shelter_data['mapped_sex_id'],
    'movementtype': shelter_data['movementtype'],
    'puttosleep': shelter_data['puttosleep'],
    'deceaseddate': shelter_data['deceaseddate']
})

final_animals_df.to_sql('animals', conn, if_exists='append', index=False)
conn.commit()

# final validation of data inserted into the tables, checking the animals table
total_loaded = cursor.execute("SELECT COUNT(*) FROM animals").fetchone()[0]
print(f"Total operational records loaded: {total_loaded}")

conn.close()
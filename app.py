from shiny import App, render, ui, reactive
import pandas as pd
import sqlite3
import os

# --- 1. DATABASE DATA INITIALIZATION ---
DB_NAME = "prod_shelter.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

# Fetch dropdown choices dynamically from lookup tables for the Intake Form
conn = get_db_connection()
species_choices = {row[0]: row[1] for row in conn.execute("SELECT species_id, species_name FROM species ORDER BY species_name").fetchall()}
color_choices = {row[0]: row[1] for row in conn.execute("SELECT color_id, color_name FROM colors ORDER BY color_name").fetchall()}
sex_choices = {row[0]: row[1] for row in conn.execute("SELECT sex_id, sex_name FROM sexes ORDER BY sex_name").fetchall()}
conn.close()


# --- 2. USER INTERFACE ---
app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h3("Log New Intake"),
        ui.input_text("ani_id", "Shelter Animal ID", placeholder="e.g. D2606012"),
        ui.input_text("ani_name", "Animal Name", placeholder="e.g. Buddy"),
        ui.input_select("ani_species", "Species", choices=species_choices),
        ui.input_text("ani_age", "Calculated Age", placeholder="e.g. 2 years"),
        ui.input_select("ani_color", "Base Colour", choices=color_choices),
        ui.input_select("ani_sex", "Sex", choices=sex_choices),
        ui.input_action_button("btn_add", "Submit Intake", class_="btn-primary"),
        title="Admin Controls"
    ),
    
    ui.card(
        ui.card_header(
            ui.layout_columns(
                ui.h2("Adoptable Shelter Animals Dashboard"),
                ui.div(
                    ui.input_action_button("btn_adopt", "Mark Selected as Adopted", class_="btn-success"),
                    style="text-align: right;"
                ),
                col_widths=[8, 4]
            )
        ),
        ui.output_data_frame("animals_grid"),
    )
)


# --- 3. SERVER LOGIC ---
def server(input, output, session):
    
    # Reactive value serving as an explicit database state trigger
    db_trigger = reactive.Value(0)
    
    # 1. READ Operation (Fixed duplication query)
    @reactive.calc
    def fetch_active_animals():
        db_trigger.get() # Establishes dependency loop
        
        conn = get_db_connection()

        query = """
            SELECT 
                MAX(a.unique_id) AS unique_id,
                a.animal_id AS [Shelter ID],
                a.animal_name AS [Name],
                a.intake_date AS [Intake Date],
                a.animal_age AS [Age],
                s.species_name AS [Species],
                c.color_name AS [Color],
                sx.sex_name AS [Sex]
            FROM animals a
            LEFT JOIN species AS s 
                ON a.species_id = s.species_id
            LEFT JOIN colors AS c
                ON a.color_id = c.color_id
            LEFT JOIN sexes AS sx
                ON a.sex_id = sx.sex_id
            WHERE a.movementtype IS NULL 
              AND a.puttosleep = 0 
              AND a.deceaseddate IS NULL
            GROUP BY a.animal_name
            ORDER BY a.animal_name ASC;
        """

        df = pd.read_sql_query(query, conn)
        conn.close()
        return df

    @render.data_frame
    def animals_grid():
        return render.DataGrid(
            fetch_active_animals(),
            editable=True,          
            selection_mode="row",   
            filters=True            
        )

    # 2. CREATE Operation (Process Sidebar Form Intake)
    @reactive.effect
    @reactive.event(input.btn_add)
    def add_new_animal():
        # Input Validation check
        if not input.ani_id() or not input.ani_name():
            ui.modal_show(ui.modal("Please fill out both the Shelter ID and Animal Name.", title="Missing Fields"))
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO animals (animal_id, animal_name, intake_date, animal_age, species_id, color_id, sex_id, puttosleep)
            VALUES (?, ?, DATE('now'), ?, ?, ?, ?, 0)
        """, (input.ani_id(), input.ani_name(), input.ani_age(), int(input.ani_species()), int(input.ani_color()), int(input.ani_sex())))
        
        conn.commit()
        conn.close()
        
        # Clear inputs for next animal entries
        ui.update_text("ani_id", value="")
        ui.update_text("ani_name", value="")
        ui.update_text("ani_age", value="")
        
        # Trigger data refresh instantly
        db_trigger.set(db_trigger.get() + 1)
        ui.modal_show(ui.modal(f"Successfully logged {input.ani_name()} into database!", title="Intake Logged"))

    # 3. DELETE / Outcome Operation (Mark selected row as adopted)
    @reactive.effect
    @reactive.event(input.btn_adopt)
    def mark_as_adopted():
        # Get selected row details from grid
        selected_rows = animals_grid.cell_selection()["rows"]
        if not selected_rows:
            ui.modal_show(ui.modal("Please select an animal from the table grid first.", title="No Selection Found"))
            return
            
        # Extract the database's true primary key (unique_id) from the selected row
        current_df = fetch_active_animals()
        selected_index = list(selected_rows)[0]
        db_unique_id = int(current_df.iloc[selected_index]["unique_id"])
        animal_name = current_df.iloc[selected_index]["Name"]
        
        # Update record row state in DB to route them out of the active grid layout
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE animals 
            SET movementtype = 'Adoption'
            WHERE unique_id = ?
        """, (db_unique_id,))
        conn.commit()
        conn.close()
        
        # Trigger refresh state update loop
        db_trigger.set(db_trigger.get() + 1)
        ui.modal_show(ui.modal(f"Congratulations! {animal_name} has been processed for adoption.", title="Animal Adopted"))

    # 4. UPDATE Operation (Direct inline cell patching)
    @animals_grid.set_patch_fn
    def upgrade_cell_patch(*, patch: render.CellPatch):
        try:
            current_df = fetch_active_animals()
            
            # Extract values using standard dictionary bracket syntax
            row_idx = patch["row_index"]
            col_idx = patch["column_index"]
            new_value = patch["value"]
            
            # Map column indices to our specific dataframe headers safely
            col_name = current_df.columns[col_idx]
            
            # Grab the database primary key for that specific row row_idx
            db_unique_id = int(current_df.iloc[row_idx]["unique_id"])
            
            # Map front-end column names back to physical database schema names
            column_mapping = {
                "Shelter ID": "animal_id",
                "Name": "animal_name",
                "Intake Date": "intake_date",
                "Age": "animal_age"
            }
            
            db_column = column_mapping.get(col_name)
            if not db_column:
                # If they try to edit Species/Color/Sex, reject it safely
                return current_df.iloc[row_idx][col_name]
                
            # Execute the UPDATE directly to SQLite
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE animals SET {db_column} = ? WHERE unique_id = ?", (new_value, db_unique_id))
            conn.commit()
            conn.close()
            
            # Return the new value string to tell the grid it was successful!
            return new_value
            
        except Exception as e:
            print(f"Database Cell Patch Failed: {e}")
            # If it errors out, return the old value to revert gracefully instead of freezing red
            return patch["value"]

app = App(app_ui, server)
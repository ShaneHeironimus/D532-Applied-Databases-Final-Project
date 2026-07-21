from shiny import App, render, ui, reactive
import pandas as pd
import sqlite3

# initializing the connection to the database
def get_db_connection():
    conn = sqlite3.connect("prod_shelter.db")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

# generating the list list of choices for each dropdown menu on the left panel menu
conn = get_db_connection()
species_choices = {row[0]: row[1] for row in conn.execute("SELECT species_id, species_name FROM species ORDER BY species_name").fetchall()}
color_choices = {row[0]: row[1] for row in conn.execute("SELECT color_id, color_name FROM colors ORDER BY color_name").fetchall()}
sex_choices = {row[0]: row[1] for row in conn.execute("SELECT sex_id, sex_name FROM sexes ORDER BY sex_name").fetchall()}
conn.close()


# creating the user interface for the dashboard
app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h3("Log New Intake"),
        ui.input_text("ani_id", "Shelter Animal ID", placeholder="e.g. 12345"),
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
                ui.h2("Bloomington Adoptable Shelter Animals Dashboard"),
                ui.div(
                    ui.input_action_button("btn_adopt", "Mark Selected as Adopted", class_="btn-success"),
                    style="text-align: right;"
                ),
                col_widths=[8, 4]
            )
        ),
        ui.output_data_frame("animals_grid"),
    )

    # TODO - add cards for total cats and dogs
)


# back end server logic, this is where the database actions will take place
def server(input, output, session):
    
    # Reactive value serving as an explicit database state trigger
    db_trigger = reactive.Value(0)
    
    @reactive.calc
    def fetch_active_animals():
        db_trigger.get()
        
        conn = get_db_connection()

        # this query generates the list of adoptable animals while ensuring
        # there are no duplicate records by using MAX() on unique_id
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

    # allow the user to enter new animals into the records
    # it also checks to make sure the fields are filled out before sending the data to the query
    @reactive.effect
    @reactive.event(input.btn_add)
    def add_new_animal():
        # Input Validation check
        if not input.ani_id() or not input.ani_name():
            ui.modal_show(ui.modal("Please fill out both the Shelter ID and Animal Name.", title="Missing Fields"))
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # this query will insert the records into the animals table based on the input of the Intake Form
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

    # this section allows the user to mark an animal as "adopted" and will remove the record from the dashboard and refresh it
    # the records are not removed from the database since they are still there for historical records
    @reactive.effect
    @reactive.event(input.btn_adopt)
    def mark_as_adopted():
        # this logic ensures that a record is selected to be "adopted" before proceeding with the action
        selected_rows = animals_grid.cell_selection()["rows"]
        if not selected_rows:
            ui.modal_show(ui.modal("Please select an animal from the table grid first.", title="No Selection Found"))
            return
            
        # grabbing the animal's unique ID to then be passed to the next query to remove the record
        current_df = fetch_active_animals()
        selected_index = list(selected_rows)[0]
        db_unique_id = int(current_df.iloc[selected_index]["unique_id"])
        animal_name = current_df.iloc[selected_index]["Name"]
        
        # this updates the database by setting the movementtype to "Adoption" to mark the animal as adopted
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE animals 
            SET movementtype = 'Adoption'
            WHERE unique_id = ?
        """, (db_unique_id,))
        conn.commit()
        conn.close()
        
        # refreshes the dashboard and displays a message about the animal being adopted
        db_trigger.set(db_trigger.get() + 1)
        ui.modal_show(ui.modal(f"Congratulations! {animal_name} has been processed for adoption.", title="Animal Adopted"))

    # this section allows the user to directly update the cells within the dashboard without needing to remove and add the record again
    @animals_grid.set_patch_fn
    def upgrade_cell_patch(*, patch: render.CellPatch):
        try:
            current_df = fetch_active_animals()
            
            # extracting values
            row_idx = patch["row_index"]
            col_idx = patch["column_index"]
            new_value = patch["value"]
            
            # mapping column indices to specific dataframe headers
            col_name = current_df.columns[col_idx]
            
            # grabbing the primary key
            db_unique_id = int(current_df.iloc[row_idx]["unique_id"])
            
            # mapping the columns to the database column names
            column_mapping = {
                "Shelter ID": "animal_id",
                "Name": "animal_name",
                "Intake Date": "intake_date",
                "Age": "animal_age"
            }
            
            db_column = column_mapping.get(col_name)
            if not db_column:
                return current_df.iloc[row_idx][col_name]
                
            # UPDATE query to make the changes to the respective selected cell
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE animals SET {db_column} = ? WHERE unique_id = ?", (new_value, db_unique_id))
            conn.commit()
            conn.close()

            return new_value
        
        except Exception as e:
            print(f"Database Cell Patch Failed: {e}")
            return patch["value"]

app = App(app_ui, server)

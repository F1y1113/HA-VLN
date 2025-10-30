import habitat_sim
import magnum as mn
import numpy as np
import cv2
import json
import os
import argparse
import threading
import time
import queue
import re

# --- Configuration ---

DATA_PATH = "/home/qw/proj/HA-VLN/Data/" 
SCENE_DATASETS_PATH = os.path.join(DATA_PATH, "scene_datasets/mp3d")
HAPS_DATA_PATH = os.path.join(DATA_PATH, "HAPS2_0")
HUMAN_ANNOTATIONS_PATH = os.path.join(DATA_PATH, "Multi-Human-Annotations/human_motion.json")


def load_glb_files(base_path):
    """Recursively load all .glb files from the given base_path, sorted numerically."""
    glb_files = []
    if not os.path.isdir(base_path):
        print(f"Warning: Human asset directory not found: {base_path}")
        return []
    for root, _, files in os.walk(base_path):
        for file in files:
            if file.endswith(".glb"):
                glb_files.append(os.path.join(root, file))

    # Sort files numerically based on numbers extracted from the filename
    def sort_key(filepath):
        filename = os.path.splitext(os.path.basename(filepath))[0]
        # Find all sequences of digits in the filename
        numbers = re.findall(r'\d+', filename)
        if numbers:
            # Use the last sequence of digits found for sorting
            return int(numbers[-1])
        else:
            # If no digits found, return 0 or handle as needed
            return 0

    glb_files.sort(key=sort_key)
    return glb_files

def make_sim_configuration(scene_id):
    """Creates a basic Habitat-Sim configuration."""
    backend_cfg = habitat_sim.SimulatorConfiguration()
    backend_cfg.gpu_device_id = 0 # Use GPU 0
    backend_cfg.scene_id = scene_id
    if not os.path.exists(backend_cfg.scene_id):
         raise FileNotFoundError(f"Scene file not found: {scene_id}")
    backend_cfg.enable_physics = True # Essential for dynamic objects

    # Agent configuration
    agent_cfg = habitat_sim.agent.AgentConfiguration()

    
    # Define sensors using habitat_sim.SensorSpec
    rgb_sensor_spec = habitat_sim.SensorSpec()
    rgb_sensor_spec.uuid = "color_sensor"
    rgb_sensor_spec.sensor_type = habitat_sim.SensorType.COLOR
    rgb_sensor_spec.resolution = [512, 512]
    rgb_sensor_spec.position = [0.0, 1.0, 0.0] # Slightly above agent center
    agent_cfg.sensor_specifications = [rgb_sensor_spec]


    # Define agent actions (adjust as needed)
    agent_cfg.action_space = {
        "move_forward": habitat_sim.agent.ActionSpec(
            "move_forward", habitat_sim.agent.ActuationSpec(amount=0.25) # 0.25m steps
        ),
        "turn_left": habitat_sim.agent.ActionSpec(
            "turn_left", habitat_sim.agent.ActuationSpec(amount=15.0) # 15 degree turns
        ),
        "turn_right": habitat_sim.agent.ActionSpec(
            "turn_right", habitat_sim.agent.ActuationSpec(amount=15.0)
        ),
    }

    return habitat_sim.Configuration(backend_cfg, [agent_cfg])

# --- Human Management Class (based on Paper Alg A2 ) ---
class HumanManager:
    def __init__(self, sim, human_data, target_scan_id, frame_interval=1/25.0):
        self.sim = sim
        self.human_data = human_data
        self.target_scan_id = target_scan_id
        self.frame_interval = frame_interval # Time between frames (e.g., 1/25 for 25 FPS)

        self.humans = [] # Stores info about active humans
        self.human_render_objects = {} # Maps human_point_id to current sim object ID
        self.current_frame_indices = {} # Maps human_point_id to current frame index
        self.object_template_ids = {} # Maps human_point_id to list of template IDs
        self.object_template_paths = {} # Maps human_point_id to list of .glb paths
        self.translations = {} # Maps human_point_id to list of translations
        self.rotations = {} # Maps human_point_id to list of rotations (Euler degrees)

        self._load_human_definitions()

        # --- Real-time Rendering Setup (Alg A2 ) ---
        self.signal_queue = queue.Queue(maxsize=120) # Max queue size
        self.total_signals_sent = 0
        self.total_signals_processed = 0
        self.stop_event = threading.Event()

        # Thread 1: Signal Sender
        self.signal_thread = threading.Thread(target=self._signal_sender_loop, daemon=True)

        # Thread 2: Main thread (updates are handled in update_humans method)

    
    def _load_human_definitions(self):
        """Loads human motion data for the target scan from the annotations."""
        obj_templates_mgr = self.sim.get_object_template_manager()
        scan_data = self.human_data.get(self.target_scan_id, {})

        if not scan_data:
            print(f"Warning: No human data found for scan {self.target_scan_id}")
            return

        print(f"Loading humans for scan {self.target_scan_id}...")
        for point_id, v in scan_data.items():
            try:
                category = v['category'].replace(' ', '_').replace('/', '_')
                index = v['index']
                translations = v['translation']
                rotations = v['rotation'] # Euler angles in degrees

                if not translations or not rotations: continue

                glb_folder_path = os.path.join(HAPS_DATA_PATH, f"{category}_{index}")
                glb_files = load_glb_files(glb_folder_path)

                if not glb_files:
                    print(f"  Warning: No GLB files found for {category}_{index} at {glb_folder_path}")
                    continue

                if len(glb_files) != len(translations) or len(glb_files) != len(rotations):
                    print(f"  Warning: Mismatch between GLB files ({len(glb_files)}), translations ({len(translations)}), and rotations ({len(rotations)}) for {point_id}. Skipping.")
                    continue

                print(f"  Loading {len(glb_files)} frames for human {point_id} ({category}_{index})...")
                template_ids = []

                # Simplify template loading - load_configs handles registration
                # by file path implicitly.
                for glb_file in glb_files:
                    # load_configs returns a list of IDs of the templates loaded
                    # The manager automatically uses the filepath as the handle
                    # and avoids duplicates.
                    loaded_template_ids = obj_templates_mgr.load_configs(str(glb_file))
                    if not loaded_template_ids:
                         raise RuntimeError(f"Failed to load template for GLB file: {glb_file}")
                    template_ids.append(loaded_template_ids[0]) # Assuming one template per file


                self.humans.append(point_id)
                self.object_template_ids[point_id] = template_ids
                self.object_template_paths[point_id] = glb_files # Keep for potential debugging
                self.translations[point_id] = translations
                self.rotations[point_id] = rotations
                self.current_frame_indices[point_id] = 0
                self.human_render_objects[point_id] = None # No object initially added
                print(f"  Successfully loaded definition for human {point_id}")

            except KeyError as e:
                print(f"  Warning: Missing key {e} for point_id {point_id}. Skipping.")
            except Exception as e:
                print(f"  Error loading human {point_id}: {e}") # Print the specific error

    def _signal_sender_loop(self):
        """Thread 1: Periodically sends refresh signals."""
        while not self.stop_event.is_set():
            if not self.signal_queue.full():
                self.signal_queue.put("REFRESH_HUMAN")
                self.total_signals_sent += 1
            time.sleep(self.frame_interval)

    def start_updates(self):
        """Starts the signal sender thread."""
        if not self.signal_thread.is_alive():
            print("Starting human update thread...")
            self.signal_thread.start()

    def stop_updates(self):
        """Stops the signal sender thread."""
        print("Stopping human update thread...")
        self.stop_event.set()
        # Clear queue to unblock thread if it's waiting on put()
        while not self.signal_queue.empty():
            try:
                self.signal_queue.get_nowait()
            except queue.Empty:
                break
        if self.signal_thread.is_alive():
            self.signal_thread.join()
        print("Human update thread stopped.")


    def update_humans(self):
        """Thread 2 action: Processes signals and updates human meshes in the simulator."""
        processed_signal_this_step = False
        while not self.signal_queue.empty():
            try:
                signal = self.signal_queue.get_nowait()
                if signal == "REFRESH_HUMAN":
                    current_global_frame = self.total_signals_processed % 120 # Assuming 120 frames loop

                    for point_id in self.humans:
                        template_ids = self.object_template_ids[point_id]
                        num_frames = len(template_ids)
                        if num_frames == 0: continue

                        frame_idx = current_global_frame % num_frames
                        template_id = template_ids[frame_idx]
                        translation = self.translations[point_id][frame_idx]
                        rotation_euler = self.rotations[point_id][frame_idx] # Euler angles in degrees

                        # Convert Euler angles (degrees) to magnum quaternion
                        rotation_quat = (
                            mn.Quaternion.rotation(mn.Deg(rotation_euler[0]), mn.Vector3.x_axis()) *
                            mn.Quaternion.rotation(mn.Deg(rotation_euler[1]), mn.Vector3.y_axis()) *
                            mn.Quaternion.rotation(mn.Deg(rotation_euler[2]), mn.Vector3.z_axis())
                        )

                        # Remove previous object instance for this human if it exists
                        existing_obj_id = self.human_render_objects.get(point_id)
                        if existing_obj_id is not None and self.sim.get_existing_object_ids():
                             if existing_obj_id in self.sim.get_existing_object_ids():
                                try:
                                    self.sim.remove_object(existing_obj_id)
                                except Exception as e:
                                    # This can happen if physics simulation removes the object due to instability
                                    # print(f"Minor issue removing object {existing_obj_id} for human {point_id}: {e}")
                                    pass

                        # Add the new object instance for the current frame
                        try:
                            new_obj_id = self.sim.add_object(template_id)
                            # --- START OF FIX ---
                            # Check against the integer value -1 for invalid ID
                            if new_obj_id == -1:
                            # --- END OF FIX ---
                                print(f"Error adding object for human {point_id} frame {frame_idx}")
                                self.human_render_objects[point_id] = None
                                continue

                            self.sim.set_translation(translation, new_obj_id)
                            self.sim.set_rotation(rotation_quat, new_obj_id)
                            self.human_render_objects[point_id] = new_obj_id
                            self.current_frame_indices[point_id] = frame_idx
                        except Exception as e:
                            print(f"Runtime error adding/setting object for human {point_id} frame {frame_idx}: {e}")
                            self.human_render_objects[point_id] = None


                    self.total_signals_processed += 1
                    processed_signal_this_step = True
                self.signal_queue.task_done()

            except queue.Empty:
                break # No more signals for now
            except Exception as e:
                 print(f"Error processing signal queue: {e}")
                 # Attempt to clear the problematic signal
                 try: self.signal_queue.task_done()
                 except: pass

        # Return True if any human mesh was updated, False otherwise
        return processed_signal_this_step

    def cleanup_humans(self):
        """Removes all human objects from the simulation."""
        print("Cleaning up human objects...")
        for point_id, obj_id in self.human_render_objects.items():
             if obj_id is not None and self.sim.get_existing_object_ids():
                 if obj_id in self.sim.get_existing_object_ids():
                    try:
                        self.sim.remove_object(obj_id)
                    except Exception as e:
                        print(f"Minor issue removing object {obj_id} during cleanup: {e}")
        self.human_render_objects = {}

# --- Main Interactive Loop ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scan", type=str, default="1LXtFkjw3qL", help="Scan ID to load (e.g., '17DRP5sb8fy')"
    )
    parser.add_argument(
        "--scene-file", type=str, default=None, help="Optional: Path to the specific .glb scene file. Overrides default path construction."
    )
    args = parser.parse_args()

    # --- 1. Load Scene ---
    if args.scene_file:
        scene_filepath = args.scene_file
    else:
        scene_filepath = os.path.join(SCENE_DATASETS_PATH, args.scan, f"{args.scan}.glb")

    print(f"Loading scene: {scene_filepath}")
    sim_cfg = make_sim_configuration(scene_filepath)
    try:
        sim = habitat_sim.Simulator(sim_cfg)
    except Exception as e:
        print(f"Failed to create simulator: {e}")
        return

    # Initialize agent position (optional, place it somewhere reasonable)
    initial_state = sim.get_agent(0).get_state()
    # Try getting a navigable point, otherwise use default
    start_pos = sim.pathfinder.get_random_navigable_point()
    initial_state.position = start_pos
    sim.get_agent(0).set_state(initial_state)
    print(f"Agent starting at: {initial_state.position}")


    # --- 2. Load Human Data ---
    try:
        with open(HUMAN_ANNOTATIONS_PATH, 'r') as f:
            all_human_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Human annotations file not found at {HUMAN_ANNOTATIONS_PATH}")
        sim.close()
        return
    except json.JSONDecodeError:
         print(f"Error: Could not parse human annotations file at {HUMAN_ANNOTATIONS_PATH}")
         sim.close()
         return

    # --- 3. Initialize Human Manager ---
    human_manager = HumanManager(sim, all_human_data, args.scan)
    human_manager.start_updates() # Start the background thread

    # --- 4. Interactive Control ---
    print("\n--- Interactive Controls ---")
    print("  W: Move Forward")
    print("  A: Turn Left")
    print("  D: Turn Right")
    print("  Q: Quit")
    print("---------------------------\n")

    cv2.namedWindow("HA-VLN Interactive", cv2.WINDOW_NORMAL)

    try:
        while True:
            # --- Update Humans ---
            # Process signals from the queue and update meshes
            human_manager.update_humans()

            # --- Step Physics (Important!) ---
            # Step physics AFTER updating human positions/meshes for the current frame
            # Use a fixed timestep (e.g., 1/60th of a second)
            sim.step_physics(1.0 / 60.0)

            # --- Get Observation ---
            obs = sim.get_sensor_observations()
            rgb_img = obs.get("color_sensor")

            if rgb_img is not None:
                # Convert RGBA to BGR for OpenCV
                bgr_img = cv2.cvtColor(rgb_img[..., :3], cv2.COLOR_RGB2BGR)
                cv2.imshow("HA-VLN Interactive", bgr_img)
            else:
                print("Warning: Could not retrieve color sensor observation.")

            # --- Handle Input ---
            key = cv2.waitKey(1) & 0xFF # Use waitKey(1) for non-blocking check

            action = None
            if key == ord('w') or key == ord('W'):
                action = "move_forward"
            elif key == ord('a') or key == ord('A'):
                action = "turn_left"
            elif key == ord('d') or key == ord('D'):
                action = "turn_right"
            elif key == ord('q') or key == ord('Q'):
                break # Quit

            # --- Perform Action ---
            if action:
                sim.step(action) # Habitat integrates physics step here for agent actions

    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        # --- Cleanup ---
        print("Shutting down...")
        human_manager.stop_updates()
        human_manager.cleanup_humans()
        sim.close()
        cv2.destroyAllWindows()
        print("Simulator closed.")

if __name__ == "__main__":
    main()
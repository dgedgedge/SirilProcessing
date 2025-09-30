#!/bin/env python3
import os
import subprocess
import logging


def run_siril_script(siril_script_content: str, working_dir: str, siril_path: str = "siril", siril_mode: str = "flatpak") -> bool:
    """
    Executes a temporary Siril script.
    
    Args:
        siril_script_content: Content of the Siril script to execute
        working_dir: Working directory for the script execution
        siril_path: Path to the Siril executable
        siril_mode: Mode for running Siril ('native', 'flatpak', or 'appimage')
    
    Returns:
        True if execution was successful, False otherwise
    """
    script_path = os.path.join(working_dir, "siril_script.sps")
    try:
        with open(script_path, "w") as f:
            f.write(siril_script_content)

        logging.info(f"Executing Siril script {script_path} in {working_dir}:\n{siril_script_content}")

        # --- Build command based on siril_mode ---
        if siril_mode == "native":
            cmd = [siril_path, "-s", script_path]
        elif siril_mode == "flatpak":
            cmd = ["flatpak", "run", "org.siril.Siril", "-s", script_path]
        elif siril_mode == "appimage":
            cmd = [siril_path, "-s", script_path]
        else:
            logging.error(f"Unknown siril_mode: {siril_mode}")
            return False

        result = subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            logging.error(f"Siril script failed with error code {result.returncode}.")
            logging.error(f"Siril stdout:\n{result.stdout}")
            logging.error(f"Siril stderr:\n{result.stderr}")
            return False
        else:
            logging.info("Siril script executed successfully.")
            logging.debug(f"Siril stdout:\n{result.stdout}")
            return True
    except FileNotFoundError:
        logging.error(f"Siril executable not found at '{siril_path}'. Please check the path.")
        return False
    except Exception as e:
        logging.error(f"Error executing Siril script: {e}")
        return False
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)  # Clean up temporary script

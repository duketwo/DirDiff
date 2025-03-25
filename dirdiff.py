#!/usr/bin/env python3
import os
import subprocess
import argparse
import tempfile
from pathlib import Path
import time
import statistics

def run_xxd_diff(file1, file2):
    """Run xxd diff between two files and return the diffstat output."""
    try:
        # Create temporary files for the xxd outputs
        with tempfile.NamedTemporaryFile() as temp1, tempfile.NamedTemporaryFile() as temp2:
            # Run xxd on both files
            subprocess.run(["xxd", file1], stdout=temp1, check=True)
            subprocess.run(["xxd", file2], stdout=temp2, check=True)
            
            # Flush to ensure all data is written
            temp1.flush()
            temp2.flush()
            
            # Run diff and diffstat on the xxd outputs
            diff_process = subprocess.Popen(
                ["diff", temp1.name, temp2.name],
                stdout=subprocess.PIPE
            )
            
            diffstat_process = subprocess.run(
                ["diffstat"],
                stdin=diff_process.stdout,
                capture_output=True,
                text=True,
                check=False
            )
            
            return diffstat_process.stdout.strip()
    except subprocess.CalledProcessError:
        return "Error comparing files"

def parse_diffstat(diffstat_output):
    """Parse the diffstat output to extract insertions and deletions."""
    if "0 files changed" in diffstat_output:
        return 0, 0
    
    # Try to extract insertions and deletions
    insertions, deletions = 0, 0
    
    if "insertion" in diffstat_output:
        try:
            ins_part = diffstat_output.split("insertion")[0]
            insertions = int(ins_part.split(",")[-1].strip())
        except (ValueError, IndexError):
            pass
    
    if "deletion" in diffstat_output:
        try:
            del_part = diffstat_output.split("deletion")[0]
            if "insertion" in del_part:
                del_part = del_part.split(",")[-2]
            else:
                del_part = del_part.split(",")[-1]
            deletions = int(del_part.strip())
        except (ValueError, IndexError):
            pass
    
    return insertions, deletions

def get_all_files(directory):
    """Get all files in a directory recursively with their relative paths."""
    files = set()
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, directory)
            files.add(rel_path)
    return files

def build_directory_tree(dir1, dir2):
    """Build a directory tree structure with file status information."""
    # Get all files from both directories
    print("Scanning directories...")
    files1 = get_all_files(dir1)
    files2 = get_all_files(dir2)
    
    print(f"Found {len(files1)} files in old directory")
    print(f"Found {len(files2)} files in new directory")
    
    # Identify added, removed, and common files
    added_files = files2 - files1
    removed_files = files1 - files2
    common_files = files1 & files2
    
    print(f"Added: {len(added_files)}, Removed: {len(removed_files)}, Common: {len(common_files)}")
    print("\nComparing files...")
    
    # Build a tree structure
    tree = {}
    all_files = sorted(files1 | files2)
    
    total_files = len(all_files)
    processed = 0
    
    # Track files with changes for statistical analysis
    changed_files_data = []
    
    for file_path in all_files:
        parts = file_path.split(os.sep)
        current = tree
        
        # Build directory structure
        for i, part in enumerate(parts):
            if i == len(parts) - 1:  # This is a file
                # Determine file status
                if file_path in added_files:
                    status = "added"
                    changes = (0, 0)
                elif file_path in removed_files:
                    status = "removed"
                    changes = (0, 0)
                else:
                    processed += 1
                    print(f"Comparing [{processed}/{total_files}]: {file_path}")
                    file1_path = os.path.join(dir1, file_path)
                    file2_path = os.path.join(dir2, file_path)
                    diffstat_output = run_xxd_diff(file1_path, file2_path)
                    insertions, deletions = parse_diffstat(diffstat_output)
                    
                    if insertions > 0 or deletions > 0:
                        status = "changed"
                        changes = (insertions, deletions)
                        # Track this file for statistical analysis
                        changed_files_data.append({
                            "path": file_path,
                            "insertions": insertions,
                            "deletions": deletions,
                            "total_changes": insertions + deletions
                        })
                    else:
                        status = "unchanged"
                        changes = (0, 0)
                
                current[part] = {"type": "file", "status": status, "changes": changes}
            else:  # This is a directory
                if part not in current:
                    current[part] = {"type": "dir", "children": {}}
                current = current[part]["children"]
    
    return tree, changed_files_data

def print_tree(tree, prefix="", is_last=True, is_root=True):
    """Print the directory tree with visual indicators."""
    if is_root:
        print("\nDirectory Tree:")
        print("└── Root")
        prefix = "    "
    
    items = list(tree.items())
    
    for i, (name, node) in enumerate(items):
        is_last_item = i == len(items) - 1
        
        # Determine the connector and next prefix
        if is_last_item:
            connector = "└── "
            next_prefix = prefix + "    "
        else:
            connector = "├── "
            next_prefix = prefix + "│   "
        
        # Print the current node
        if node["type"] == "file":
            status_indicator = ""
            if node["status"] == "added":
                status_indicator = "[+] "
            elif node["status"] == "removed":
                status_indicator = "[-] "
            elif node["status"] == "changed":
                ins, dels = node["changes"]
                changes = []
                if ins > 0:
                    changes.append(f"+{ins}")
                if dels > 0:
                    changes.append(f"-{dels}")
                change_str = ", ".join(changes)
                status_indicator = f"[~] ({change_str}) "
            
            print(f"{prefix}{connector}{status_indicator}{name}")
        else:  # It's a directory
            print(f"{prefix}{connector}{name}/")
            print_tree(node["children"], next_prefix, is_last_item, False)

def count_status(tree):
    """Count files by status in the tree."""
    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    
    def count_recursive(node):
        if node["type"] == "file":
            counts[node["status"]] += 1
        else:
            for child in node["children"].values():
                count_recursive(child)
    
    for node in tree.values():
        count_recursive(node)
    
    return counts

def identify_significant_changes(changed_files_data, threshold_multiplier=2.0):
    """Identify files with significantly more changes than average."""
    if not changed_files_data:
        return []
    
    # Calculate statistics
    total_changes = [file_data["total_changes"] for file_data in changed_files_data]
    
    try:
        mean_changes = statistics.mean(total_changes)
        stdev_changes = statistics.stdev(total_changes) if len(total_changes) > 1 else 0
    except statistics.StatisticsError:
        return []
    
    # Set threshold as mean + (multiplier * stdev)
    threshold = mean_changes + (threshold_multiplier * stdev_changes)
    
    # Find files with changes above threshold
    significant_files = [
        file_data for file_data in changed_files_data
        if file_data["total_changes"] > threshold
    ]
    
    # Sort by total changes (descending)
    significant_files.sort(key=lambda x: x["total_changes"], reverse=True)
    
    return significant_files, mean_changes, threshold

def main():
    parser = argparse.ArgumentParser(description="Compare files between two directories")
    parser.add_argument("dir1", help="First directory (older)")
    parser.add_argument("dir2", help="Second directory (newer)")
    parser.add_argument("--threshold", type=float, default=2.0, 
                        help="Multiplier for standard deviation to identify significant changes (default: 2.0)")
    args = parser.parse_args()
    
    # Check if directories exist
    if not os.path.isdir(args.dir1):
        print(f"Error: {args.dir1} is not a directory")
        return 1
    
    if not os.path.isdir(args.dir2):
        print(f"Error: {args.dir2} is not a directory")
        return 1
    
    print(f"Comparing directories:")
    print(f"  Old: {args.dir1}")
    print(f"  New: {args.dir2}")
    
    start_time = time.time()
    
    # Build and print the directory tree
    tree, changed_files_data = build_directory_tree(args.dir1, args.dir2)
    print_tree(tree)
    
    # Print summary
    counts = count_status(tree)
    print("\nSummary:")
    print(f"  Added files:     {counts['added']}")
    print(f"  Removed files:   {counts['removed']}")
    print(f"  Changed files:   {counts['changed']}")
    print(f"  Unchanged files: {counts['unchanged']}")
    
    # Identify and print files with significant changes
    if changed_files_data:
        significant_files, mean_changes, threshold = identify_significant_changes(
            changed_files_data, args.threshold
        )
        
        if significant_files:
            print("\nFiles with Significant Changes:")
            print(f"  (Average changes per file: {mean_changes:.2f}, Threshold: {threshold:.2f})")
            print("-" * 80)
            
            for file_data in significant_files:
                path = file_data["path"]
                ins = file_data["insertions"]
                dels = file_data["deletions"]
                total = file_data["total_changes"]
                
                print(f"  {path}")
                print(f"    Changes: {total} total (+{ins}, -{dels})")
                print(f"    {total/mean_changes:.1f}x the average change rate")
                print()
    
    elapsed_time = time.time() - start_time
    print(f"\nComparison completed in {elapsed_time:.2f} seconds")
    
    return 0

if __name__ == "__main__":
    exit(main())

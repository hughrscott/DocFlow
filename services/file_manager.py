"""
File Manager Service for DocFlow.

Handles all file operations including moving, copying, organizing, and
secure deletion of documents. Manages file system operations safely.
"""

import os
import shutil
import logging
from typing import Dict, Any, Tuple, Optional, List
from pathlib import Path
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)


class FileManager:
    """
    Manages all file operations for DocFlow.
    
    Responsibilities:
    - Move files to organized folders
    - Copy files safely
    - Generate unique filenames to avoid conflicts
    - Secure deletion of temporary files
    - Track file operations
    """
    
    def __init__(self, root_documents_dir: str):
        """
        Initialize file manager with root directory.
        
        Args:
            root_documents_dir: Root directory for organized documents
        """
        self.root_dir = Path(root_documents_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"FileManager initialized with root: {self.root_dir}")
    
    def move_file(
        self,
        source_path: str,
        dest_folder: str,
        dest_filename: str,
        overwrite: bool = False
    ) -> Tuple[bool, str, str]:
        """
        Move a file from source to destination folder with new name.
        
        Args:
            source_path: Full path to source file
            dest_folder: Destination folder (relative to root)
            dest_filename: New filename
            overwrite: Whether to overwrite if file exists
            
        Returns:
            Tuple of (success: bool, full_path: str, message: str)
            Example: (True, "/path/to/file.pdf", "File moved successfully")
        """
        try:
            source = Path(source_path)
            
            if not source.exists():
                error_msg = f"Source file not found: {source_path}"
                logger.error(error_msg)
                return False, "", error_msg
            
            # Create destination folder if needed
            dest_dir = self.root_dir / dest_folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            # Handle filename conflicts
            final_filename = dest_filename
            if not overwrite:
                final_filename = self._get_unique_filename(dest_dir, dest_filename)
            
            dest_path = dest_dir / final_filename
            
            logger.info(f"Moving file: {source} → {dest_path}")
            
            # Move the file
            shutil.move(str(source), str(dest_path))
            
            success_msg = f"File moved to {dest_folder}/{final_filename}"
            logger.info(success_msg)
            
            return True, str(dest_path), success_msg
        
        except PermissionError as e:
            error_msg = f"Permission denied moving file: {str(e)}"
            logger.error(error_msg)
            return False, "", error_msg
        
        except Exception as e:
            error_msg = f"Error moving file: {str(e)}"
            logger.error(error_msg)
            return False, "", error_msg
    
    def copy_file(
        self,
        source_path: str,
        dest_folder: str,
        dest_filename: str,
        overwrite: bool = False
    ) -> Tuple[bool, str, str]:
        """
        Copy a file from source to destination folder.
        
        Useful for keeping originals while creating copies for organization.
        
        Args:
            source_path: Full path to source file
            dest_folder: Destination folder (relative to root)
            dest_filename: New filename
            overwrite: Whether to overwrite if file exists
            
        Returns:
            Tuple of (success: bool, full_path: str, message: str)
        """
        try:
            source = Path(source_path)
            
            if not source.exists():
                error_msg = f"Source file not found: {source_path}"
                logger.error(error_msg)
                return False, "", error_msg
            
            # Create destination folder if needed
            dest_dir = self.root_dir / dest_folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            # Handle filename conflicts
            final_filename = dest_filename
            if not overwrite:
                final_filename = self._get_unique_filename(dest_dir, dest_filename)
            
            dest_path = dest_dir / final_filename
            
            logger.info(f"Copying file: {source} → {dest_path}")
            
            # Copy the file
            shutil.copy2(str(source), str(dest_path))
            
            success_msg = f"File copied to {dest_folder}/{final_filename}"
            logger.info(success_msg)
            
            return True, str(dest_path), success_msg
        
        except PermissionError as e:
            error_msg = f"Permission denied copying file: {str(e)}"
            logger.error(error_msg)
            return False, "", error_msg
        
        except Exception as e:
            error_msg = f"Error copying file: {str(e)}"
            logger.error(error_msg)
            return False, "", error_msg
    
    def _get_unique_filename(self, folder: Path, filename: str) -> str:
        """
        Generate a unique filename if file already exists.
        
        If "document.pdf" exists, returns "document-2.pdf", "document-3.pdf", etc.
        
        Args:
            folder: Destination folder
            filename: Desired filename
            
        Returns:
            Unique filename
        """
        filepath = folder / filename
        
        if not filepath.exists():
            return filename
        
        # Split filename and extension
        name_parts = filename.rsplit('.', 1)
        if len(name_parts) == 2:
            base_name, extension = name_parts
        else:
            base_name, extension = filename, ""
        
        # Try numbered versions
        counter = 2
        while True:
            if extension:
                new_name = f"{base_name}-{counter}.{extension}"
            else:
                new_name = f"{base_name}-{counter}"
            
            if not (folder / new_name).exists():
                logger.debug(f"Generated unique filename: {new_name}")
                return new_name
            
            counter += 1
            
            # Safety check to prevent infinite loop
            if counter > 1000:
                # Use timestamp as fallback
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                new_name = f"{base_name}-{timestamp}.{extension}" if extension else f"{base_name}-{timestamp}"
                logger.warning(f"Too many conflicts, using timestamp: {new_name}")
                return new_name
    
    def create_folder(self, folder_path: str) -> Tuple[bool, str]:
        """
        Create a folder at the specified path.
        
        Args:
            folder_path: Folder path relative to root (e.g., "Banking/Personal/PNC")
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            full_path = self.root_dir / folder_path
            full_path.mkdir(parents=True, exist_ok=True)
            
            if full_path.exists():
                logger.info(f"Folder created/exists: {full_path}")
                return True, f"Folder ready: {folder_path}"
            else:
                error_msg = f"Failed to create folder: {folder_path}"
                logger.error(error_msg)
                return False, error_msg
        
        except PermissionError as e:
            error_msg = f"Permission denied creating folder: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
        
        except Exception as e:
            error_msg = f"Error creating folder: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def secure_delete(self, file_path: str, shred_passes: int = 1) -> Tuple[bool, str]:
        """
        Securely delete a file by overwriting it before deletion.
        
        Useful for temporary files containing sensitive information.
        
        Args:
            file_path: Full path to file to delete
            shred_passes: Number of overwrite passes (1-3)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            filepath = Path(file_path)
            
            if not filepath.exists():
                warning_msg = f"File not found for deletion: {file_path}"
                logger.warning(warning_msg)
                return True, warning_msg
            
            file_size = filepath.stat().st_size
            
            # Overwrite file with random data
            logger.debug(f"Securely deleting file: {file_path} ({shred_passes} passes)")
            
            for pass_num in range(shred_passes):
                with open(filepath, 'rb+') as f:
                    # Overwrite with random data
                    random_data = os.urandom(file_size)
                    f.write(random_data)
                    f.flush()
                    os.fsync(f.fileno())
            
            # Delete the file
            filepath.unlink()
            
            success_msg = f"File securely deleted: {file_path}"
            logger.info(success_msg)
            return True, success_msg
        
        except PermissionError as e:
            error_msg = f"Permission denied deleting file: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
        
        except Exception as e:
            error_msg = f"Error deleting file: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """
        Get information about a file.
        
        Args:
            file_path: Full path to file
            
        Returns:
            Dictionary with file information
        """
        try:
            filepath = Path(file_path)
            
            if not filepath.exists():
                return {"exists": False, "error": "File not found"}
            
            stat = filepath.stat()
            
            # Calculate file hash
            file_hash = self._calculate_file_hash(filepath)
            
            return {
                "exists": True,
                "path": str(filepath),
                "name": filepath.name,
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "hash": file_hash,
            }
        
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            return {"exists": False, "error": str(e)}
    
    def _calculate_file_hash(self, filepath: Path, algorithm: str = "md5") -> str:
        """
        Calculate hash of file for verification.
        
        Args:
            filepath: Path to file
            algorithm: Hash algorithm (md5, sha256, etc.)
            
        Returns:
            Hex digest of file hash
        """
        try:
            if algorithm == "md5":
                hasher = hashlib.md5()
            elif algorithm == "sha256":
                hasher = hashlib.sha256()
            else:
                hasher = hashlib.md5()
            
            # Read file in chunks
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hasher.update(chunk)
            
            return hasher.hexdigest()
        
        except Exception as e:
            logger.error(f"Error calculating file hash: {e}")
            return ""
    
    def organize_document(
        self,
        source_file: str,
        folder_path: str,
        filename: str,
        move: bool = True
    ) -> Dict[str, Any]:
        """
        Complete workflow to organize a document.
        
        Takes a temporary file and moves/copies it to its final organized location.
        
        Args:
            source_file: Full path to source file
            folder_path: Destination folder (relative to root)
            filename: Final filename
            move: If True, move file; if False, copy file
            
        Returns:
            Dictionary with operation results:
            {
                "success": bool,
                "source": str,
                "destination": str,
                "filename": str,
                "folder": str,
                "message": str,
                "file_info": dict
            }
        """
        try:
            logger.info(f"Organizing document: {filename} → {folder_path}")
            
            # Create folder
            success, msg = self.create_folder(folder_path)
            if not success:
                return {
                    "success": False,
                    "source": source_file,
                    "destination": "",
                    "filename": "",
                    "folder": folder_path,
                    "message": f"Failed to create folder: {msg}",
                    "file_info": {}
                }
            
            # Move or copy file
            if move:
                success, dest_path, msg = self.move_file(source_file, folder_path, filename)
            else:
                success, dest_path, msg = self.copy_file(source_file, folder_path, filename)
            
            if not success:
                return {
                    "success": False,
                    "source": source_file,
                    "destination": dest_path,
                    "filename": filename,
                    "folder": folder_path,
                    "message": msg,
                    "file_info": {}
                }
            
            # Get file info
            file_info = self.get_file_info(dest_path)
            
            return {
                "success": True,
                "source": source_file,
                "destination": dest_path,
                "filename": Path(dest_path).name,
                "folder": folder_path,
                "message": msg,
                "file_info": file_info
            }
        
        except Exception as e:
            error_msg = f"Error organizing document: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "source": source_file,
                "destination": "",
                "filename": filename,
                "folder": folder_path,
                "message": error_msg,
                "file_info": {}
            }
    
    def list_files_in_folder(
        self,
        folder_path: str,
        recursive: bool = False,
        pattern: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List files in a folder.
        
        Args:
            folder_path: Folder path relative to root
            recursive: If True, search subfolders recursively
            pattern: Optional glob pattern to filter files (e.g., "*.pdf")
            
        Returns:
            List of file information dictionaries
        """
        try:
            folder = self.root_dir / folder_path
            
            if not folder.exists():
                logger.warning(f"Folder not found: {folder_path}")
                return []
            
            files = []
            
            if recursive and pattern:
                search_pattern = f"**/{pattern}"
            elif recursive:
                search_pattern = "**/*"
            elif pattern:
                search_pattern = pattern
            else:
                search_pattern = "*"
            
            for filepath in folder.glob(search_pattern):
                if filepath.is_file():
                    files.append({
                        "path": str(filepath),
                        "relative_path": str(filepath.relative_to(self.root_dir)),
                        "name": filepath.name,
                        "size_bytes": filepath.stat().st_size,
                        "modified": datetime.fromtimestamp(filepath.stat().st_mtime).isoformat()
                    })
            
            logger.debug(f"Found {len(files)} files in {folder_path}")
            return sorted(files, key=lambda x: x['modified'], reverse=True)
        
        except Exception as e:
            logger.error(f"Error listing files: {e}")
            return []
    
    def get_folder_size(self, folder_path: str) -> Dict[str, Any]:
        """
        Calculate total size of a folder and its contents.
        
        Args:
            folder_path: Folder path relative to root
            
        Returns:
            Dictionary with size information
        """
        try:
            folder = self.root_dir / folder_path
            
            if not folder.exists():
                return {"exists": False, "error": "Folder not found"}
            
            total_size = 0
            file_count = 0
            
            for filepath in folder.rglob('*'):
                if filepath.is_file():
                    total_size += filepath.stat().st_size
                    file_count += 1
            
            return {
                "exists": True,
                "path": str(folder),
                "total_bytes": total_size,
                "total_mb": round(total_size / (1024 * 1024), 2),
                "total_gb": round(total_size / (1024 * 1024 * 1024), 2),
                "file_count": file_count
            }
        
        except Exception as e:
            logger.error(f"Error calculating folder size: {e}")
            return {"exists": False, "error": str(e)}

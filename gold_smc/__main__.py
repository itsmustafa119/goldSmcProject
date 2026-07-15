from .config import acquire_instance_lock
from .core import main

if __name__ == "__main__":
    # Prevent multiple dashboard instances on Windows
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        print(
            "Dashboard is already running. "
            "Only one instance is allowed at a time."
        )
        exit(1)
    
    main()

from config import config

print(f"provider = '{config.provider}'")
print(f"model    = '{config.model}'")
print(f"api_key  = {'configured' if config.api_key else 'missing'}")
print(f"base_url = '{config.base_url}'")
print(f"detected = '{config.provider_name}'")
print(f"context  = {config.context_window}")

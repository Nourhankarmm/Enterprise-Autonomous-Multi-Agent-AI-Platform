import os
from alembic import context
from sqlalchemy import create_engine, pool
from platform_api.database import metadata

config = context.config
url = config.attributes.get('database_url') or os.getenv('DATABASE_URL') or config.get_main_option('sqlalchemy.url')
if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

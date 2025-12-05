all: hello

hello:
	python -c "open('hello','w').write('Hello from make-simple!')"

install:
	python -c "import os,sys; prefix=os.environ.get('DESTDIR',''); import os as _os; prefix = prefix or _os.path.expanduser('~'); bin_dir = _os.path.join(prefix, 'bin'); _os.makedirs(bin_dir, exist_ok=True); open(_os.path.join(bin_dir, 'hello'), 'w').write('Hello from make-simple!'); print('Installed to', bin_dir)"

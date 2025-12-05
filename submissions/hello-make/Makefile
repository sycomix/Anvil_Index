CC = cc
PREFIX ?= /usr/local
BINDIR = $(PREFIX)/bin

all: hello

hello: hello.c
	$(CC) -o hello hello.c

install:
	mkdir -p $(DESTDIR)$(BINDIR)
	cp hello $(DESTDIR)$(BINDIR)

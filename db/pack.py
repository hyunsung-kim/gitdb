"""Module containing a database to deal with packs"""
from base import (
						FileDBBase, 
						ObjectDBR
				)

from gitdb.util import (
							to_bin_sha, 
							LazyMixin
						)

from gitdb.exc import (
							BadObject,
							UnsupportedOperation,
						)

from gitdb.pack import PackEntity

import os
import glob
__all__ = ('PackedDB', )


#{ Utilities


class PackedDB(FileDBBase, ObjectDBR, LazyMixin):
	"""A database operating on a set of object packs"""
	
	# sort the priority list every N queries
	# Higher values are better, performance tests don't show this has 
	# any effect, but it should have one
	_sort_interval = 500
	
	def __init__(self, root_path):
		super(PackedDB, self).__init__(root_path)
		# list of lists with three items:
		# * hits - number of times the pack was hit with a request
		# * entity - Pack entity instance
		# * sha_to_index - PackIndexFile.sha_to_index method for direct cache query
		# self._entities = list()		# lazy loaded list
		self._hit_count	= 0				# amount of hits
		self._st_mtime = 0				# last modification data of our root path
		
	def _set_cache_(self, attr):
		# currently it can only be our _entities attribute
		self._entities = list()
		self.update_pack_entity_cache()
		
	def _sort_entities(self):
		self._entities.sort(key=lambda l: l[0], reverse=True)
		
	def _pack_info(self, sha):
		""":return: tuple(entity, index) for an item at the given sha
		:param sha: 20 or 40 byte sha
		:raise BadObject:
		:note: This method is not thread-safe, but may be hit in multi-threaded
			operation. The worst thing that can happen though is a counter that 
			was not incremented, or the list being in wrong order. So we safe
			the time for locking here, lets see how that goes"""
		# presort ?
		if self._hit_count % self._sort_interval == 0:
			self._sort_entities()
		# END update sorting
		
		sha = to_bin_sha(sha)
		for item in self._entities:
			index = item[2](sha)
			if index is not None:
				item[0] += 1			# one hit for you
				self._hit_count += 1	# general hit count
				return (item[1], index)
			# END index found in pack
		# END for each item
		
		# no hit, see whether we have to update packs
		# NOTE: considering packs don't change very often, we safe this call
		# and leave it to the super-caller to trigger that
		raise BadObject(sha)
	
	#{ Object DB Read 
	
	def has_object(self, sha):
		try:
			self._pack_info(sha)
			return True
		except BadObject:
			return False
		# END exception handling
		
	def info(self, sha):
		entity, index = self._pack_info(sha)
		return entity.info_at_index(index)
	
	def stream(self, sha):
		entity, index = self._pack_info(sha)
		return entity.stream_at_index(index)
	
	#} END object db read
	
	#{ object db write
	
	def store(self, istream):
		"""Storing individual objects is not feasible as a pack is designed to 
		hold multiple objects. Writing or rewriting packs for single objects is
		inefficient"""
		raise UnsupportedOperation()
		
	def store_async(self, reader):
		# TODO: add ObjectDBRW before implementing this
		raise NotImplementedError()
	
	#} END object db write
	
	
	#{ Interface 
	
	def update_pack_entity_cache(self, force=False):
		"""Update our cache with the acutally existing packs on disk. Add new ones, 
		and remove deleted ones. We keep the unchanged ones
		:param force: If True, the cache will be updated even though the directory
			does not appear to have changed according to its modification timestamp.
		:return: True if the packs have been updated so there is new information, 
			False if there was no change to the pack database"""
		stat = os.stat(self.root_path())
		if not force and stat.st_mtime <= self._st_mtime:
			return False
		# END abort early on no change
		self._st_mtime = stat.st_mtime
		
		# packs are supposed to be prefixed with pack- by git-convention
		# get all pack files, figure out what changed
		pack_files = set(glob.glob(os.path.join(self.root_path(), "pack-*.pack")))
		our_pack_files = set(item[1].pack().path() for item in self._entities)
		
		# new packs
		for pack_file in (pack_files - our_pack_files):
			# init the hit-counter/priority with the size, a good measure for hit-
			# probability. Its implemented so that only 12 bytes will be read
			entity = PackEntity(pack_file)
			self._entities.append([entity.pack().size(), entity, entity.index().sha_to_index])
		# END for each new packfile
		
		# removed packs
		for pack_file in (our_pack_files - pack_files):
			del_index = -1
			for i, item in enumerate(self._entities):
				if item[1].pack().path() == pack_file:
					del_index = i
					break
				# END found index
			# END for each entity
			assert del_index != -1
			del(self._entities[del_index])
		# END for each removed pack
		
		# reinitialize prioritiess
		self._sort_entities()
		return True
		
	def entities(self):
		""":return: list of pack entities operated upon by this database"""
		return [ item[1] for item in self._entities ]
	
	def sha_iter(self):
		"""Return iterator yielding 20 byte shas for the packed objects in this data base"""
		sha_list = list()
		for entity in self.entities():
			index = entity.index()
			sha_by_index = index.sha
			for index in xrange(index.size()):
				yield sha_by_index(index)
			# END for each index
		# END for each entity
	
	def size(self):
		""":return: amount of packed objects in this database"""
		sizes = [item[1].index().size() for item in self._entities]
		return reduce(lambda x,y: x+y, sizes)
	#} END interface
#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Copyright (c) 2011 Tyler Kenendy <tk@tkte.ch>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from .topping import Topping
from burger.util import string_from_invokedymanic

from jawa.constants import String

import traceback

# We can identify almost every class we need just by
# looking for consistent strings.
MATCHES = (
    (['Fetching addPacket for removed entity', 'Fetching packet for removed entity'], 'entity.trackerentry'),
    (['#%04d/%d%s', 'attribute.modifier.equals.'], 'itemstack'),
    (['disconnect.lost'], 'nethandler.client'),
    (['Outdated server!', 'multiplayer.disconnect.outdated_client'],
        'nethandler.server'),
    (['Corrupt NBT tag'], 'nbtcompound'),
    ([' is already assigned to protocol '], 'packet.connectionstate'),
    (
        ['The received encoded string buffer length is ' \
        'less than zero! Weird string!'],
        'packet.packetbuffer'
    ),
    (['Data value id is too big'], 'metadata'),
    (['X#X'], 'recipe.superclass'),
    (['Skipping BlockEntity with id '], 'tileentity.superclass'),
    (
        ['ThreadedAnvilChunkStorage ({}): All chunks are saved'],
        'anvilchunkloader'
    ),
    (['has invalidly named property'], 'blockstatecontainer'),
    ((['bubble'], True), 'particletypes'),
    (['No value with id '], 'idmap')
)

# Enforce a lower priority on some matches, since some classes may match both
# these and other strings, which we want to be grouped with the other string
# if it exists, and with this if it doesn't
MAYBE_MATCHES = (
    (['Skipping Entity with id'], 'entity.list'),
)

# In some cases there really isn't a good way to verify that it's a specific
# class and we need to just depend on it coming first (bad!)
# The biome class specifically is an issue because in 18w06a, the old name is
# present in the biome's own class, but the ID is still in the register class.
# This stops being an issue later into 1.13 when biome names become translatable.

# Similarly, in 1.13, "bubble" is ambiguous between the particle class and
# particle list, but the particletypes topping works with the first result in that case.

# In 1.18-pre8, the "Getting block state" message now appears in both rendering
# code and world code, but in both cases the return type is correct.
IGNORE_DUPLICATES = [ "biome.register", "particletypes", "blockstate" ]

def check_match(value, match_list):
    exact = False
    if isinstance(match_list, tuple):
        match_list, exact = match_list

    for match in match_list:
        if exact:
            if value != match:
                continue
        else:
            if match not in value:
                continue

        return True
    return False

def identify(classloader, path, verbose):
    """
    The first pass across the jar will identify all possible classes it
    can, maping them by the 'type' it implements.

    We have limited information available to us on this pass. We can only
    check for known signatures and predictable constants. In the next pass,
    we'll have the initial mapping from this pass available to us.
    """
    possible_match = None

    for c in classloader.search_constant_pool(path=path, type_=String):
        value = c.string.value
        for match_list, match_name in MATCHES:
            if check_match(value, match_list):
                class_file = classloader[path]
                return match_name, class_file.this.name.value

        for match_list, match_name in MAYBE_MATCHES:
            if check_match(value, match_list):
                class_file = classloader[path]
                possible_match = (match_name, class_file.this.name.value)
                # Continue searching through the other constants in the class

        if 'as a Component' in value:
            # This class is the JSON serializer/deserializer for the chat component.
            # (This string constant exists starting in 13w36a (1.7.2))

            class_file = classloader[path]
            # First, look for the method referencing that constant...
            for method in class_file.methods:
                for ins in method.code.disassemble():
                    if ins.mnemonic in ("ldc", "ldc_w"):
                        if isinstance(ins.operands[0], String) and 'as a Component' in ins.operands[0].string.value:
                            # This method is the serializing one.
                            # The chatcomponent type is its first parameter.
                            return 'chatcomponent', method.args[0][2]
                    elif ins.mnemonic == "invokedynamic":
                        if 'as a Component' in string_from_invokedymanic(ins, class_file):
                            return 'chatcomponent', method.args[0][2]
            else:
                if verbose:
                    print("Found chat component serializer as %s, but didn't find the method serializes it" % path)

        if value == 'ambient.cave':
            # This is found in both the sounds list class and sounds event class.
            # However, the sounds list class also has a constant specific to it.
            # Note that this method will not work in 1.8, but the list class doesn't exist then either.
            class_file = classloader[path]

            for c2 in class_file.constants.find(type_=String):
                if c2 == 'Accessed Sounds before Bootstrap!':
                    return 'sounds.list', class_file.this.name.value
            else:
                return 'sounds.event', class_file.this.name.value

        if value == 'piston_head':
            # piston_head is a technical block, which is important as that means it has no item form.
            # This constant is found in both the block list class and the class containing block registrations.
            class_file = classloader[path]

            for c2 in class_file.constants.find(type_=String):
                if c2 == 'doTileDrops':
                    # not in the list, only in registry
                    return 'block.register', class_file.this.name.value
            else:
                for c2 in class_file.constants.find(type_=String):
                    if c2 == 'Tesselating block in world':
                        # Rendering code, which we don't care about
                        return
                else:
                    return 'block.list', class_file.this.name.value

        if value == 'diamond_pickaxe':
            # Similarly, diamond_pickaxe is only an item.  This exists in 3 classes, though:
            # - The actual item registration code
            # - The item list class
            # - The item renderer class (until 1.13), which we don't care about
            class_file = classloader[path]

            for c2 in class_file.constants.find(type_=String):
                if c2 == 'textures/misc/enchanted_item_glint.png':
                    # Item renderer, which we don't care about
                    return

                if c2 == 'CB3F55D3-645C-4F38-A497-9C13A33DB5CF':
                    # Item registry always contains this uuid for
                    # "BASE_ATTACK_DAMAGE_UUID"
                    return 'item.register', class_file.this.name.value
            else:
                return 'item.list', class_file.this.name.value

        if value in ('Ice Plains', 'mutated_ice_flats', 'ice_spikes'):
            # Finally, biomes.  There's several different names that were used for this one biome
            # Only classes are the list class and the one with registration.  Note that the list didn't exist in 1.8.
            class_file = classloader[path]

            for c2 in class_file.constants.find(type_=String):
                if c2 == 'Accessed Biomes before Bootstrap!':
                    return 'biome.list', class_file.this.name.value
            else:
                return 'biome.register', class_file.this.name.value

        if value == 'minecraft':
            class_file = classloader[path]

            # Look for two protected/private final strings
            def is_protected_final_or_private_final(m):
                # 22w42a/1.19.3+ makes it private instead of protected
                return (m.access_flags.acc_protected or m.access_flags.acc_private) and m.access_flags.acc_final

            find_args = {
                "type_": "Ljava/lang/String;",
                "f": is_protected_final_or_private_final
            }
            fields = class_file.fields.find(**find_args)

            if len(list(fields)) == 2:
                return 'identifier', class_file.this.name.value

        if value == 'PooledMutableBlockPosition modified after it was released.':
            # Keep on going up the class hierarchy until we find a logger,
            # which is declared in the main BlockPos class
            # We can't hardcode a specific number of classes to go up, as
            # in some versions PooledMutableBlockPos extends BlockPos directly,
            # but in others have PooledMutableBlockPos extend MutableBlockPos.
            # Also, this is the _only_ string constant available to us.
            # Finally, note that PooledMutableBlockPos was introduced in 1.9.
            # This technique will not work in 1.8.
            cf = classloader[path]
            logger_type = "Lorg/apache/logging/log4j/Logger;"
            while not cf.fields.find_one(type_=logger_type):
                if cf.super_.name == "java/lang/Object":
                    cf = None
                    break
                cf = classloader[cf.super_.name.value]
            if cf:
                return 'position', cf.this.name.value

        if value == 'Getting block state':
            # This message is found in Chunk, in the method getBlockState.
            # We could also theoretically identify BlockPos from this method,
            # but currently identify only allows marking one class at a time.
            class_file = classloader[path]

            for method in class_file.methods:
                for ins in method.code.disassemble():
                    if ins.mnemonic in ("ldc", "ldc_w"):
                        if ins.operands[0] == 'Getting block state':
                            return 'blockstate', method.returns.name
            else:
                if verbose:
                    print("Found chunk as %s, but didn't find the method that returns blockstate" % path)

        if value == 'particle.notFound':
            # This is in ParticleArgument, which is used for commands and
            # implements brigadier's ArgumentType<IParticleData>.
            class_file = classloader[path]

            if len(class_file.interfaces) == 1 and class_file.interfaces[0].name == "com/mojang/brigadier/arguments/ArgumentType":
                sig = class_file.attributes.find_one(name="Signature").signature.value
                inner_type = sig[sig.index("<") + 1 : sig.rindex(">")][1:-1]
                return "particle", inner_type
            elif verbose:
                print("Found ParticleArgument as %s, but it didn't implement the expected interface" % path)

        if value == 'HORIZONTAL':
            # In 22w43a, there is a second enum with HORIZONTAL and VERTICAL as members (used in UI
            # code), not just enumfacing.plane. They can be differentiated by the constructors.
            # This constructor was added in 1.13.
            # Prior to 1.13, the string "Someone's been tampering with the universe!" indicates
            # enumfacing.plane. After, it instead indicates the x/y/z axis. So, if we don't find
            # a matching constructor, check for that string constant instead. That string constant
            # was removed entirely in 1.18 (it existed in 1.17). I'm not sure of which specific
            # snapshots this was changed in.
            class_file = classloader[path]

            def is_enumfacing_plane_constructor(m):
                # We're looking for EnumFacing$Plane(EnumFacing[], EnumFacing$Axis[]).
                # Java synthetically adds parameters for enum name and ordinal, so that constructor
                # has 4 parameters, with the last 2 being arrays.
                return len(m.args) == 4 and m.args[2].dimensions == 1 and m.args[3].dimensions==1

            if len(list(class_file.methods.find(name="<init>", f=is_enumfacing_plane_constructor))) != 0:
                return "enumfacing.plane", class_file.this.name.value
            for c2 in class_file.constants.find(type_=String):
                if c2 == "Someone's been tampering with the universe!":
                    return "enumfacing.plane", class_file.this.name.value

    # May (will usually) be None
    return possible_match


class IdentifyTopping(Topping):
    """Finds important superclasses needed by other toppings."""

    PROVIDES = [
        "identify.anvilchunkloader",
        "identify.biome.list",
        "identify.biome.register",
        "identify.block.list",
        "identify.block.register",
        "identify.blockstatecontainer",
        "identify.blockstate",
        "identify.chatcomponent",
        "identify.entity.list",
        "identify.entity.trackerentry",
        "identify.enumfacing.plane",
        "identify.identifier",
        "identify.idmap",
        "identify.item.list",
        "identify.item.register",
        "identify.itemstack",
        "identify.metadata",
        "identify.nbtcompound",
        "identify.nethandler.client",
        "identify.nethandler.server",
        "identify.packet.connectionstate",
        "identify.packet.packetbuffer",
        "identify.particle",
        "identify.particletypes",
        "identify.position",
        "identify.recipe.superclass",
        "identify.sounds.event",
        "identify.sounds.list",
        "identify.tileentity.superclass"
    ]

    DEPENDS = []

    @staticmethod
    def act(aggregate, classloader, verbose=False):
        classes = aggregate.setdefault("classes", {})
        for path in classloader.path_map.keys():
            if not path.endswith(".class"):
                continue

            result = identify(classloader, path[:-len(".class")], verbose)
            if result:
                if result[0] in classes:
                    if result[0] in IGNORE_DUPLICATES:
                        continue
                    raise Exception(
                            "Already registered %(value)s to %(old_class)s! "
                            "Can't overwrite it with %(new_class)s" % {
                                "value": result[0],
                                "old_class": classes[result[0]],
                                "new_class": result[1]
                            })
                classes[result[0]] = result[1]
                if len(classes) == len(IdentifyTopping.PROVIDES):
                    # If everything has been found, we don't need to keep
                    # searching, so stop early for performance
                    break

        # Add classes that might not be recognized in some versions
        # since the registration class is also the list class
        if "sounds.list" not in classes and "sounds.event" in classes:
            classes["sounds.list"] = classes["sounds.event"]
        if "block.list" not in classes and "block.register" in classes:
            classes["block.list"] = classes["block.register"]
        if "item.list" not in classes and "item.register" in classes:
            classes["item.list"] = classes["item.register"]
        if "biome.list" not in classes and "biome.register" in classes:
            classes["biome.list"] = classes["biome.register"]

        if verbose:
            print("identify classes: %s" % classes)

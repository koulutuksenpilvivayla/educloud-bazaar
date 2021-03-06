import urllib2, os, sys
import html2text
from PIL import Image, ImageChops
from django.views.decorators.csrf import csrf_exempt
import uuid as libuuid
from rest_framework.renderers import UnicodeJSONRenderer
from django.http import HttpResponse
#from django.contrib.auth.models import User, Group
from django.contrib.auth import get_user_model
from django.db.models import Count
from rest_framework import viewsets
from apps.api.serializers import UserSerializer, GroupSerializer, APINodeSerializer, ProductSerializer, ProductTypeSerializer, SubjectSerializer, ProductPurchasedSerializer, ProductFormatSerializer
from django.db import IntegrityError
from rest_framework.views import APIView
from rest_framework import authentication, permissions
from rest_framework.response import Response
#from apps.catalogue import models as catalogueModels
from oscar.core.loading import get_class, get_model
from apps.api import models
from datetime import datetime
import json
from django.template.defaultfilters import slugify
from rest_framework import authentication, permissions
from rest_framework.authentication import OAuth2Authentication, BasicAuthentication, SessionAuthentication
from apps.api.permissions import IsOwner
from rest_framework import generics
from rest_framework import permissions
from django.http import Http404

from errorcodes import *

User = get_user_model()
Product = get_model('catalogue', 'Product')
ProductCategory = get_model('catalogue', 'ProductCategory')
Language = get_model('catalogue', 'Language')
Tag = get_model('catalogue', 'Tags')
EmbeddedMedia = get_model('catalogue', 'EmbeddedMedia')
Category = get_model('catalogue', 'Category')
ProductClass = get_model('catalogue', 'ProductClass')
Partner = get_model('partner', 'Partner')
StockRecord = get_model('partner', 'StockRecord')
ProductPurchased = get_model('library', 'ProductPurchased')
ProductFormat = get_model('catalogue', 'ProductFormat')

#success messages:
postSuccess = {"message" : "Operation successful."}
putSuccess = {"message" : "Operation successful."}

# Create your views here.
# API-views
class ProductTypeList(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint that lists the producttypes available for POST and PUT calls
    of materials.
    """
    queryset = ProductClass.objects.all()
    serializer_class = ProductTypeSerializer
    paginate_by = 100
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)

class SubjectList(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint that lists the subjects available for POST and PUT calls
    of materials.
    """
    queryset = Category.objects.all()
    serializer_class = SubjectSerializer
    paginate_by = 100
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)

class ProductFormatList(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint that lists the productFormats available for POST and PUT calls
    of materials.
    """
    queryset = ProductFormat.objects.all()
    serializer_class = ProductFormatSerializer
    paginate_by = 100
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)


# this view is used to handle all CMS interaction through collections
# and resources
class CMSView(APIView):
    """
    API endpoint which allows to GET, POST and PUT materials for CMS. The system resembles
    a file-system where there is collections and resources. Every CMS has one root-collection where
    they can create new materialitems. Sample requests and in-depth usage documentation can be found at:
    https://github.com/koulutuksenpilvivayla/pilvivayla-basaari/wiki/API-Definition

    Using of this API requires a oAuth2 token which can be received from Bazaar's admin.
    """
    authentication_classes = (OAuth2Authentication, BasicAuthentication, SessionAuthentication)
    permission_classes = (permissions.IsAuthenticatedOrReadOnly, IsOwner)

    def splitUrl(self, url):
        splitpath = url.lower().split('/')
        splitpath = splitpath[0:]
        splitpath = filter(None,splitpath)

        for x in range(0, len(splitpath)):
            splitpath[x] = slugify(splitpath[x])    #remove bad characters from url
        return splitpath

    def slugifyWholeUrl(self, url):
        urlarray = self.splitUrl(url)
        url= urlarray[0]
        for i in range(1, len(urlarray)):
            url += "/" + urlarray[i]
        return url

    #check whether there is empty strings in the url
    def isValidUrl(self,path):
        splitpath = self.trimTheUrl(path)
        print splitpath
        if len(splitpath) == 0:
            raise UrlIsEmpty()


    #trim unnecessary part of url
    def trimTheUrl(self,path):
        url = path
        url = url[len("/api/cms/"):] #slice the useless part away
        #slice the trailing:
        url = url.strip("/")
        return url

    def checkIfAlreadyInDb(self, path):
        return models.APINode.objects.filter(uniquePath=self.slugifyWholeUrl(path)).exists()

    #make sure there isn't items in the middle of the given path
    def checkIfItemsInPostPath(self, path):
        urlTokens = self.splitUrl(path)
        pathSoFar = "" #urlTokens[0]
        for i in range(0, len(urlTokens)):
            print pathSoFar
            pathSoFar = pathSoFar.strip("/")
            if models.APINode.objects.filter(uniquePath=pathSoFar, objectType="item").exists():
                #we found an object which is an item and in middle of the given path.
                #because items can't have children, this is an ERROR condition.
                return True
            else:
                pathSoFar += "/" + urlTokens[i]

        if models.APINode.objects.filter(uniquePath=pathSoFar, objectType="item").exists():
            #we found an object which is an item and in middle of the given path.
            #because items can't have children, this is an ERROR condition.
            return True
        return False

    #after we have verified that the url can be used to create new item or collection, check
    #the path and list not existing collections to be created.
    #If collection exists already, nothing happens.
    def createCollections(self, path, request):
        urlTokens = self.splitUrl(path)
        parentPathSoFar = ""
        pathSoFar = urlTokens[0]
        createdCollection = []

        #check if the first collection exists. If not, throw exception:
        if models.APINode.objects.filter(uniquePath=pathSoFar, objectType="collection").exists() == False:
            raise RootError()

        for i in range(1, len(urlTokens)+1):

            try:
                node = models.APINode.objects.get(uniquePath=pathSoFar, objectType="collection")
                perm = IsOwner()
                if not perm.has_object_permission(request, self, node):
                    raise AuthError()

                #the collection exists, therefore it doesn't need to be created.
                parentPathSoFar = pathSoFar
                if i < len(urlTokens):
                    pathSoFar += "/" + urlTokens[i] #move to the next

            except models.APINode.DoesNotExist:
                #the collection doesn't exist yet so create it:
                newColl = models.APINode.create(pathSoFar, parentPathSoFar, "collection")
                newColl.owner = request.user
                newColl.save()
                parentPathSoFar = pathSoFar
                createdCollection.append(pathSoFar)

                if i < len(urlTokens):
                    pathSoFar += "/" + urlTokens[i] #move to the next

        return unicode(createdCollection)

    def str2Bool(self, s, fieldName):
        if s == "false":
            return False
        elif s == "true":
            return True
        else:
            raise IncorrectBooleanField(fieldName)

    #check whether json has items or not
    def checkJsonData(self,request):
        theList = request.DATA
        if len(theList) == 0:
            raise NoJSON()
        try:
            theItems = request.DATA["items"]
        except:
            raise NoJSON()

    def postMaterialItem(self, path, request):
        theList = request.DATA["items"]
        createdApiNodes = []
        createdUrls = []
        createdProducts = []
        createdStockRecords = []

        try:
            #TODO: WRITE A PROPER SERIALIZER FOR THIS!!!!!!!!!!!!!!!!!
            for x in theList:
                #Add product into database
                try:
                    itemClass = ProductClass.objects.get(slug=x["productType"])
                except:
                    raise ProductTypeNotFound(x["productType"])

                #Create unique UPC
                createdUPC = self.createUPC()

                #check optional fields
                if "moreInfoUrl" in x:
                    moreInfoUrl = x["moreInfoUrl"]
                else:
                    moreInfoUrl = None

                if "productFormat" in x:
                    try:
                        pformat = ProductFormat.objects.get(slug=x["productFormat"])
                    except:
                        raise ProductFormatNotFound(x["productFormat"])
                else:
                    pformat = ProductFormat.objects.get(slug="other")

                if x["price"] < 0:
                    raise BadPrice()



                visible = self.str2Bool(x["visible"], "visible")
                stripped_description = html2text.html2text(x["description"])
                product = Product(title=x["title"], upc=createdUPC, description=stripped_description, materialUrl=x["materialUrl"],
                                  moreInfoUrl=moreInfoUrl,  uuid=x["uuid"], version=x["version"],
                                  maximumAge=x["maximumAge"], minimumAge=x["minimumAge"], contentLicense=x["contentLicense"],
                                  dataLicense=x["dataLicense"], copyrightNotice=x["copyrightNotice"], attributionText=x["attributionText"],
                                  attributionURL=x["attributionURL"],visible=visible, product_class=itemClass, product_format=pformat)


                #Add fullfilment into database
                author = Partner.objects.get(code=self.splitUrl(path)[0])

                if "contributionDate" in x:
                    try:
                        product.contributionDate = datetime.strptime(x["contributionDate"], "%Y-%m-%d")
                    except ValueError:
                        raise InvalidDate()
                else:
                    product.contributionDate = None

                if self.checkIfAlreadyInDb(path + "/" + slugify(product.uuid)):
                    raise ObjectAlreadyExists( path + "/" + slugify(product.uuid) )

                #Download icon if one is specified
                product.iconUrl = x["iconUrl"]
                product.saveIcon()
                product.save()
                createdProducts.append(product)

                #find subjects:
                subs = x["subjects"]
                if len(subs) == 0:
                    raise WrongAmountOfSubjects()
                if len(subs) > 5:
                    raise WrongAmountOfSubjects()

                for subject in subs:
                    if subs.count(subject) > 1:
                        raise EachSubjectOnlyOnce()

                for subject in subs:
                    if Category.objects.filter(slug=subject).exists():
                            category = Category.objects.get(slug=subject)
                            newProductCategory = ProductCategory(product=product, category=category)
                            newProductCategory.save()
                    else:
                        raise SubjectNotFound(subject)


                #create language, Tags and EmbeddedMedia models
                langList = x["language"]
                for lan in langList:
                    print lan
                    #check if the language is already in db, if not create it
                    if Language.objects.filter(name=lan).exists():
                        l = Language.objects.get(name=lan)
                        l.hasLanguage.add(product)
                    else:
                        langEntry = Language.create()
                        langEntry.name = lan
                        langEntry.save()
                        langEntry.hasLanguage.add(product)

                #tags creation
                if "tags" in x:
                    tagList = x["tags"]

                    if len(tagList) > 10:
                        raise TooMuchTags()

                    for tag in tagList:
                        #check if the tag is already in db, if not create it
                        if Tag.objects.filter(name=tag).exists():
                            t = Tag.objects.get(name=tag)
                            t.hasTags.add(product)
                        else:
                            tagEntry = Tag.create()
                            tagEntry.name = tag
                            tagEntry.save()
                            tagEntry.hasTags.add(product)

                #oEmbed
                if "embedMedia" in x:
                    embedList = x["embedMedia"]
                    for media in embedList:
                        print media
                        embedEntry = EmbeddedMedia.create()
                        embedEntry.url = media
                        embedEntry.product = product
                        embedEntry.save()

                #TODO: THIS IS BAD. BUT IT IS NECESSARY TO FIX ONE BAD ZERO PRICE BUG FOR NOW. PLEASE FIX THIS
                #TODO: ASAP. WE ARE SORRY ;__;
                if x["price"] == 0:
                    f = StockRecord(product=product, partner=author, price_excl_tax=0.01, price_retail=0.01, partner_sku=x["uuid"], num_in_stock=1)
                else:
                    f = StockRecord(product=product, partner=author, price_excl_tax=x["price"], price_retail=x["price"], partner_sku=x["uuid"], num_in_stock=1)
                f.save()
                createdStockRecords.append(f)


                #add APINode for this materialItem
                finalUrl = path + "/" + slugify(product.uuid)
                newColl = models.APINode.create(finalUrl, path, "item")
                newColl.materialItem = product
                newColl.owner = request.user
                newColl.save()
                createdApiNodes.append(newColl)
                createdUrls.append(finalUrl)
        except RollbackException as e:
            self.doRollback(createdApiNodes, createdProducts, createdStockRecords)
            raise e
        except Exception, e:
            #Rollback the process because of an other error
            self.doRollback(createdApiNodes, createdProducts, createdStockRecords)
            raise e
        return createdUrls

    #this function cancels all the operations done in post if there is an exception
    def doRollback(self, createdApiNodes, createdProducts, createdStockRecords):
        for node in createdApiNodes:
            node.delete()

        for product in createdProducts:
            self.unlinkTags(product)
            self.unlinkSubjects(product)
            self.unlinkLanguages(product)
            self.removeoEmbeds(product)
            product.delete()

        for stock in createdStockRecords:
            stock.delete()


    def get(self, request):

        try:
            isValid = self.isValidUrl(request.path)

            url = self.trimTheUrl(request.path)
            print url

            try:
                target = models.APINode.objects.get(uniquePath=url)
                perm = IsOwner()
                perm.has_object_permission(request, self, target)
                #check is the APINode collection or item:
                if target.objectType == "item":
                    #return JSON data of the materialItem:
                    serializer = ProductSerializer(target.materialItem)
                    return Response(serializer.data)
                else:
                    #find objects in this collection
                    children = models.APINode.objects.filter(parentPath=target.uniquePath)
                    serializer = APINodeSerializer(children, many=True, context={'request': request})
                    return Response(serializer.data)

            except models.APINode.DoesNotExist:
                raise ObjectNotFound(url)

        except RollbackException as e:
            return Response(e.msg)


    @csrf_exempt
    def post(self,request):

        try:
            self.isValidUrl(request.path)
            self.checkJsonData(request)

            url = self.trimTheUrl(request.path)

            #check if the object exists in the db already:
            url = self.slugifyWholeUrl(url)

            if self.checkIfItemsInPostPath(url):
                raise ItemOnPath()

            #create collections if needed
            self.createCollections(url, request)

            #try to create a new item:
            try:
                createdItems = self.postMaterialItem(url, request)
            except IntegrityError:
                raise UuidAlreadyExists(url)
            except KeyError as e:
                raise MissingField(e.message)

        except RollbackException as e:
            print e.apiCode
            return Response(e.getDict(), status=e.httpStatus)

        msg = postSuccess.copy()
        msg["created"] = createdItems
        return Response(msg)


    def put(self,request):
        try:
            self.isValidUrl(request.path)
            url = self.trimTheUrl(request.path)
            print url

            if models.APINode.objects.filter(uniquePath=url).exists():
                node = models.APINode.objects.get(uniquePath=url)
                if node.objectType == "item":
                    target = node.materialItem
                    try:
                        self.updateExistingItem(target, request.DATA)
                    except KeyError as e:
                        raise MissingField(e.message)

                    msg = putSuccess.copy()
                    msg["updated"] = url
                    return Response(msg)
                else:
                    raise CantUpdateCollection()
            else:
                raise ObjectNotFound(url)

        except RollbackException as e:
            return Response(e.getDict(), status=e.httpStatus)


    #updates an existing Product with data provided in the request
    def updateExistingItem(self,obj, DATA):
        obj.title = DATA["title"]
        obj.description = html2text.html2text(DATA["description"])
        obj.materialUrl = DATA["materialUrl"]
        obj.version = DATA["version"]

        visible = self.str2Bool(DATA["visible"], "visible")
        obj.visible = visible

        try:
            itemClass = ProductClass.objects.get(slug=DATA["productType"])
            obj.product_class = itemClass
        except ProductClass.DoesNotExist:
            raise ProductTypeNotFound(DATA["productType"])

        if "productFormat" in DATA:
            try:
                pformat = ProductFormat.objects.get(slug=DATA["productFormat"])
                obj.product_format = pformat
            except:
                raise ProductFormatNotFound(DATA["productFormat"])
        else:
            obj.product_format = ProductFormat.objects.get(slug="other")

        if "contributionDate" in DATA:
            try:
                obj.contributionDate = datetime.strptime(DATA["contributionDate"], "%Y-%m-%d")
            except ValueError:
                raise InvalidDate()
        else:
            obj.contributionDate = None

        if DATA["price"] < 0:
            raise BadPrice()

        if "moreInfoUrl" in DATA:
            obj.moreInfoUrl = DATA["moreInfoUrl"]
        else:
            obj.moreInfoUrl = None

        obj.maximumAge = DATA["maximumAge"]
        obj.minimumAge = DATA["minimumAge"]
        obj.contentLicense = DATA["contentLicense"]
        obj.dataLicense = DATA["dataLicense"]
        obj.copyrightNotice = DATA["copyrightNotice"]
        obj.attributionText = DATA["attributionText"]
        obj.attributionURL = DATA["attributionURL"]

        #update subject
        subs = DATA["subjects"]
        if len(subs) == 0:
            raise WrongAmountOfSubjects()
        if len(subs) > 5:
            raise WrongAmountOfSubjects()
        for subject in subs:
            if subs.count(subject) > 1:
                raise EachSubjectOnlyOnce()

        #check if the given subjects are valid ones
        for subject in subs:
            if not Category.objects.filter(slug=subject).exists():
                raise SubjectNotFound(subject)

        #remove existing subject links
        self.unlinkSubjects(obj)

        #create new ones
        for subject in subs:
            category = Category.objects.get(slug=subject)
            newProductCategory = ProductCategory(product=obj, category=category)
            newProductCategory.save()

        stock = StockRecord.objects.get(product=obj)
        stock.price_retail = DATA["price"]
        stock.save()

        #Remove existing relationships to this material from tags
        self.unlinkTags(obj)

        if "tags" in DATA:
            tagList = DATA["tags"]

            if len(tagList) > 10:
                raise TooMuchTags()

            for tag in tagList:
                print tag
                #check if the tag is already in db, if not create it
                if Tag.objects.filter(name=tag).exists():
                    t = Tag.objects.get(name=tag)
                    t.hasTags.add(obj)
                else:
                    tagEntry = Tag.create()
                    tagEntry.name = tag
                    tagEntry.save()
                    tagEntry.hasTags.add(obj)

        #oEmbed
        #remove existing embed urls
        self.removeoEmbeds(obj)

        if "embedMedia" in DATA:
            embedList = DATA["embedMedia"]
            #create new ones
            for media in embedList:
                embedEntry = EmbeddedMedia.create()
                embedEntry.url = media
                embedEntry.product = obj
                embedEntry.save()


        #Remove existing relationships to this material from languages
        self.unlinkLanguages(obj)
        langList = DATA["language"]

        for lang in langList:
            #check if the language is already in db, if not create it
            if Language.objects.filter(name=lang).exists():
                    l = Language.objects.get(name=lang)
                    l.hasLanguage.add(obj)
            else:
                langEntry = Language.create()
                langEntry.name = lang
                langEntry.save()
                langEntry.hasLanguage.add(obj)

        if "iconUrl" in DATA and DATA["iconUrl"] is not None:
            #Download icon if one is specified
            obj.iconUrl = DATA["iconUrl"]
            obj.saveIcon()
        obj.save()

    def removeoEmbeds(self, product):
        existing = EmbeddedMedia.objects.filter(product=product)
        for e in existing:
            e.delete()

    def unlinkTags(self, product):
        existingTags = Tag.objects.filter(hasTags=product)
        for t in existingTags:
            t.hasTags.remove(product)

    def unlinkSubjects(self, product):
        #remove existing subject links
        oldPCategs = ProductCategory.objects.filter(product=product)
        for pc in oldPCategs:
            pc.delete()

    def unlinkLanguages(self, product):
        existingLangs = Language.objects.filter(hasLanguage=product)
        for l in existingLangs:
            l.hasLanguage.remove(product)

    #Create unique UPC for material
    def createUPC(self):
        UPC = str(libuuid.uuid4())
        UPC = UPC.replace("-", "")
        UPC = UPC[0:10]

        while models.Product.objects.filter(upc=UPC).exists():
            UPC = str(libuuid.uuid4())
            UPC = UPC.replace("-", "")
            UPC = UPC[0:15]

        return UPC




# find purchased products of the user with oid
class PurchasedProductsView(APIView):
    """
    This API endpoint returns a list of purchased products of a user identified with provided oid.
    Sample request body for POST:
    {
        "oid":"1234"
    }

    Using of this API requires a oAuth2 token which can be received from Bazaar's admin.

    Full API-documentation:
    https://github.com/koulutuksenpilvivayla/pilvivayla-basaari/wiki/API-Definition

    """
    authentication_classes = (OAuth2Authentication, BasicAuthentication, SessionAuthentication)
    permission_classes = (permissions.IsAuthenticatedOrReadOnly, IsOwner)

    def post(self, request):

        if "oid" in request.DATA:
            if User.objects.filter(oid=request.DATA["oid"]).exists():
                user = User.objects.get(oid=request.DATA["oid"])
                visibleProducts = Product.objects.filter(visible=True)
                products = ProductPurchased.objects.filter(product__in=set(visibleProducts), owner=user, validated=True)
                serializer = ProductPurchasedSerializer(products, context={'request': request})
                return Response(serializer.data)
            else:
                d = {}
                d["message"] = "Error: No user with this oid in the database."
                d["errorcode"] = 100
                return Response(d, status=404)
        else:
            d = {}
            d["message"] = "Error: No oid provided. Please provide json-field in form { 'oid': '1234'}"
            d["errorcode"] = 102
            return Response(d, status=400)




#get product metadata for the lms
class ProductMetadataView(APIView):
    """
    This API endpoint returns the metadata of the product with the uuid.
    Using of this API requires a oAuth2 token which can be received from Bazaar's admin.

    Full API-documentation:
    https://github.com/koulutuksenpilvivayla/pilvivayla-basaari/wiki/API-Definition
    """
    authentication_classes = (OAuth2Authentication, BasicAuthentication, SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated, IsOwner)

    def get(self, request, uuid):

        if Product.objects.filter(uuid=uuid).exists():
            product = Product.objects.get(uuid=uuid)
            serializer = ProductSerializer(product)
            return Response(serializer.data)
        else:
            d = {}
            d["message"] = "Error: No material with this uuid in the database."
            d["errorcode"] = 101
            return Response(d, status=200)


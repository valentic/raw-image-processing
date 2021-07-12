#!/usr/bin/env python

#######################################################################
#
#   Image Processing Core Engine for MANGO
#   (Midlatitude All-sky-imager Network for Geophysical Observation)
#   2013-01-23  Rohit Mundra
#               Initial implementation
#
#   2013-02-05  Rohit Mundra
#               Robust Star Removal implemented
#               Histogram Equalization implemented
#
#   2013-02-22  Rohit Mundra
#               Changing unwarping to map to Normal Mercator Projection
#               Added alpha mask and PIL PNG saving
#               
#   2013-02-22  Fabrice Kengne
#               Adding compatibility for FITS read
#
#   2014-10-25  Asti Bhatt
#		Adding compatibility for BU software generated .153 files
#		
#######################################################################


from scipy.interpolate import griddata

import numpy as np
import PIL.Image
import os
import copy
import skimage.transform
import pandas as pd
from skimage import exposure


class MANGOimage:
    def __init__(self, dirpaths, rawimg):
        # list_of_dirs = ['parent', 'rawData', 'rawSite', 'rawSiteFiles', 'rawImages', 'processedImages']
        self.rawImage = rawimg
        self.parentPath = dirpaths['parent']
        self.rawDataPath = dirpaths['rawData']
        self.rawSitePath = dirpaths['rawSite']
        self.rawSiteFilesPath = dirpaths['rawSiteFiles']
        self.rawImagesPath = dirpaths['rawImages']
        self.rawImageAddress = os.path.join(self.rawImagesPath, rawimg)
        self.processedImagesFolder = dirpaths['processedImages']
        try:
            self.loadFITS()
        except ValueError:
            return
        self.process()

    def loadFITS(self):
        # cannot open .129 file using skimage

        f = open(self.rawImageAddress, "rb")
        a = np.fromfile(f, dtype='int16')
        self.imageData1D = a[64:360769].astype('int32')
        self.imageData = self.imageData1D.reshape([519, 695])
        self.width = self.imageData.shape[0]
        self.height = self.imageData.shape[1]

    def process(self):
        # self.getSiteName()
        self.loadCalibrationData()
        self.loadNewIJ()
        self.loadBackgroundCorrection()
        self.equalizeHistogram()
        self.equalizeHistogram_trial()
        # self.removeStars()
        self.setLensFunction()
        self.calibrate()
        self.mercatorUnwrap(self.imageData)
        self.writePNG()

    def loadCalibrationData(self):
        calibrationFilename = os.path.join(self.rawSiteFilesPath, 'calibration', 'Calibration.csv')
        calibrationData = pd.read_csv(calibrationFilename, delimiter=',', index_col='Star Name')
        self.azimuth = np.array(calibrationData['Azimuth'])
        self.elevation = np.array(calibrationData['Elevation'])
        self.i = np.array(calibrationData['i Coordinate'])
        self.j = np.array(calibrationData['j Coordinate'])
        self.zenith = np.array(calibrationData.loc['Zenith', ['i Coordinate', 'j Coordinate']])
        self.zenithI = self.zenith[0]
        self.zenithJ = self.zenith[1]

    def loadNewIJ(self):
        newIFilename = os.path.join(self.rawSiteFilesPath, 'calibration', 'newI.csv')
        newJFilename = os.path.join(self.rawSiteFilesPath, 'calibration', 'newJ.csv')
        nif_df = pd.read_csv(newIFilename, delimiter=',', header=None)
        self.newIMatrix = np.array(nif_df)
        njf_df = pd.read_csv(newJFilename, delimiter=',', header=None)
        self.newJMatrix = np.array(njf_df)

    def loadBackgroundCorrection(self):
        backgroundCorrectionFilename = os.path.join(self.rawSiteFilesPath, 'calibration', 'backgroundCorrection.csv')
        bg_corr_df = pd.read_csv(backgroundCorrectionFilename, delimiter=',', header=None)
        self.backgroundCorrection = np.array(bg_corr_df)

    def equalizeHistogram(self):
        # Histogram Equalization to adjust contrast [1%-99%]
        # i think i really need to understand this better
        # contrast = 99
        numberBins = 10000  # A good balance between time and space complexity, and well as precision
        imageHistogram, bins = np.histogram(self.imageData1D, numberBins)
        imageHistogram = imageHistogram[1:]
        bins = bins[1:]
        cdf = np.cumsum(imageHistogram)
        cdf = cdf[:9996]

        max_cdf = max(cdf)
        maxIndex = np.argmin(abs(cdf - 0.99 * max_cdf))
        minIndex = np.argmin(abs(cdf - 0.01 * max_cdf))
        vmax = float(bins[maxIndex])
        vmin = float(bins[minIndex])
        lowValueIndices = self.imageData1D < vmin
        self.imageData1D[lowValueIndices] = vmin
        highValueIndices = self.imageData1D > vmax
        self.imageData1D[highValueIndices] = vmax
        self.imageData = self.imageData1D.reshape(self.imageData.shape)
        # return self.imageData

    def equalizeHistogram_trial(self):
        # Contrast stretching

        self.imageData1D_t = self.imageData1D.astype('uint8')
        self.imageData_t = self.imageData1D_t.reshape([519, 695])
        self.width = self.imageData_t.shape[0]
        self.height = self.imageData_t.shape[1]

        p1, p99 = np.percentile(self.imageData_t, (1, 99))
        img_rescale = exposure.rescale_intensity(self.imageData_t, in_range=(p1, p99))
        # Equalization
        img_eq = exposure.equalize_hist(self.imageData_t, nbins=10000)
        self.imageData_t = img_eq

    def removeStars(self):
        filteredData = copy.copy(self.imageData).astype(float)
        for i in range(self.width):
            leftPixels = self.imageData[:, max(i - 5, 0):max(i - 2, 1)]
            rightPixels = self.imageData[:, min(i + 3, self.width - 2):min(i + 6, self.width - 1)]
            allPixels = np.append(leftPixels, rightPixels, 1)
            pixelData = self.imageData[:, i]
            stdVal = np.std(allPixels, 1)
            meanVal = np.mean(allPixels, 1)
            for j in range(self.height):
                if pixelData[j] > meanVal[j] + 3 * stdVal[j]:
                    filteredData[j - 1:j + 2, i - 1:i + 2] = -1
                (iKnown, jKnown) = np.where(filteredData >= 0)
            valuesKnown = np.extract(filteredData >= 0, filteredData)
        # find minimum value from the imageData to find threshold
        m = np.empty(self.width)
        for i in range(self.width):
            m[i] = min(self.imageData[:, i])
        threshold = min(m)
        (iAll, jAll) = np.where(filteredData >= threshold)  # min value from imageData
        self.imageData = np.reshape(
            griddata((iKnown, jKnown), valuesKnown, (iAll, jAll), method='linear', fill_value=0), filteredData.shape)

    def setLensFunction(self):
        # Calculates lens function coefficients for the equation: Angle = a0 + a1.px + a2.px^2 + a3.px^3
        xDiff = self.zenithI - self.i
        yDiff = self.zenithJ - self.j
        distanceFromZenith = np.sqrt(xDiff ** 2 + yDiff ** 2)
        angleFromZenith = self.elevation[0] - self.elevation
        firstColumn = np.ones(len(distanceFromZenith))

        distanceFromZenithMat = np.vstack(
            [firstColumn, distanceFromZenith, distanceFromZenith ** 2, distanceFromZenith ** 3]).transpose()
        self.pixToAngleCoefficients = np.flip(np.dot(np.linalg.pinv(distanceFromZenithMat), angleFromZenith))

        angleFromZenithMat = np.vstack(
            [firstColumn, angleFromZenith, angleFromZenith ** 2, angleFromZenith ** 3]).transpose()
        self.angleToPixCoefficients = np.flip(np.dot(np.linalg.pinv(angleFromZenithMat), distanceFromZenith))

        self.fisheyeRadius = self.getPixelsFromAngle(90)

    def getPixelsFromAngle(self, angle):
        # Input Angle in degrees
        return np.polyval(self.angleToPixCoefficients, angle)

    def calibrate(self):
        # Spatial Calibration based on star data
        # self.zenith = np.array([self.i[0], self.j[0]])
        G_el = 1.0 - self.getPixelsFromAngle(self.elevation) / self.fisheyeRadius
        self.f = G_el * np.sin(np.radians(self.azimuth))
        self.g = G_el * np.cos(np.radians(self.azimuth))
        firstColumn = np.ones(len(self.i))
        oneIJ = np.vstack((firstColumn, self.i, self.j)).transpose()

        intermediate0 = np.dot(oneIJ.transpose(), oneIJ)
        intermediate1 = np.linalg.pinv(intermediate0)
        intermediate2 = np.dot(intermediate1, oneIJ.transpose())

        aCoefficients = np.dot(intermediate2, self.f)
        bCoefficients = np.dot(intermediate2, self.g)

        self.a0 = aCoefficients[0]
        self.a1 = aCoefficients[1]
        self.a2 = aCoefficients[2]
        self.b0 = bCoefficients[0]
        self.b1 = bCoefficients[1]
        self.b2 = bCoefficients[2]

        rotationAngle_1 = np.degrees(np.arctan(-self.b1 / self.a1))
        rotationAngle_2 = np.degrees(np.arctan(self.a2 / self.b2))
        self.rotationAngle = .5 * (rotationAngle_1 + rotationAngle_2)
        self.imageData = np.fliplr(skimage.transform.rotate(self.imageData,
                                                            self.rotationAngle, order=3)).astype(float)
        self.imageData_t = np.fliplr(skimage.transform.rotate(self.imageData_t,
                                                              self.rotationAngle, order=3)).astype(float)
        # Rotating Zenith Counter-clockwise by rotation angle and flipping it left-right
        zenithI = self.width - int(np.cos(np.radians(self.rotationAngle)) * (self.zenith[0] - self.width / 2) - np.sin(
            np.radians(self.rotationAngle)) * (self.zenith[1] - self.height / 2) + self.width / 2)
        zenithJ = int(np.sin(np.radians(self.rotationAngle)) * (self.zenith[0] - self.width / 2) + np.cos(
            np.radians(self.rotationAngle)) * (self.zenith[1] - self.height / 2) + self.height / 2)
        self.zenith = [zenithI, zenithJ]
        self.setLensFunction()

        # why do we call the lens function after calibration?

    def mercatorUnwrap(self, ID_array):
        newImageWidth = 500
        newImageHeight = 500
        finalImage = np.ones([newImageHeight, newImageWidth]) * -1

        for j in range(ID_array.shape[0]):
            for i in range(ID_array.shape[1]):
                newI = self.newIMatrix[j][i]
                newJ = self.newJMatrix[j][i]
                if not (np.isnan(newI)) and not (np.isnan(newJ)) and not \
                        (np.isnan(self.backgroundCorrection[j, i])):
                    ID_array[j, i] = ID_array[j, i] * self.backgroundCorrection[j, i]
                    finalImage[int(newJ), int(newI)] = ID_array[j, i]

        (iKnown, jKnown) = np.where(finalImage >= 0)
        valuesKnown = np.extract(finalImage >= 0, finalImage)
        (iAll, jAll) = np.where(finalImage >= -1)
        interpolatedData = np.reshape(
            griddata((iKnown, jKnown), valuesKnown, (iAll, jAll), method='cubic', fill_value=-1),
            [newImageWidth, newImageHeight])
        alphaMask = np.ones([newImageHeight, newImageWidth]) * 255
        for j in range(interpolatedData.shape[0]):
            for i in range(interpolatedData.shape[1]):
                if interpolatedData[j, i] == -1:
                    # alphaMask[j, i] = 0
                    alphaMask[j, i] = np.nan  # to create transparent background

        interpolatedData = (interpolatedData * 255 / (np.nanmax(interpolatedData))).astype('uint8')
        alphaMask = alphaMask.astype('uint8')
        ID_array = np.dstack([interpolatedData, alphaMask])
        self.writeMode = 'LA'
        self.imageData = ID_array

    def writePNG(self):
        writeAddress = os.path.join(self.processedImagesFolder, self.rawImage[0:8] + ".png")

        finalImage = PIL.Image.fromarray(self.imageData, self.writeMode)

        finalImage.save(writeAddress, format='png')
